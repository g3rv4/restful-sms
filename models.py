from peewee import *
from config import settings
from datetime import datetime, timedelta
from unidecode import unidecode
import telnetlib
import binascii
import json
import requests

#SMS States
CREATED = 0
PROCESSED = 10
FAILED = 20
CREDIT_REQUEST_SENT = 30
TIMED_OUT = 40

#SMS Directions
INCOMING = 0
OUTGOING = 10

sqlsettings = settings['mysql']
database = MySQLDatabase(sqlsettings['database'], **{'user': sqlsettings['username'], 'passwd': sqlsettings['password'], 'host': sqlsettings['host'], 'port': sqlsettings['port']})

def get_ascii_string(original):
    return original.encode('ascii')

class UnknownFieldType(object):
    pass

class TelnetClient(telnetlib.Telnet):
    def __init__(self, host=None, port=0,
                 timeout=telnetlib.socket._GLOBAL_DEFAULT_TIMEOUT):
        super().__init__(host, port, timeout)

    def write(self, buffer):
        super().write(buffer.encode('ascii'))

    def read_until(self, match, timeout=None):
        #TODO: Throw an exception if it times out
        return super().read_until(match.encode('ascii'), timeout).decode("utf-8")

class BaseModel(Model):
    class Meta:
        database = database

class ATServer(BaseModel):
    ip = CharField(max_length=15)
    password = CharField(max_length=15)
    port = IntegerField(null=True)
    username = CharField(max_length=15)

    class Meta:
        db_table = 'at_servers'

class LocalNumber(BaseModel):
    module = IntegerField()
    number = CharField(max_length=15)
    server = ForeignKeyField(rel_model=ATServer, db_column='server_id', related_name='local_numbers')

    class Meta:
        db_table = 'local_numbers'

class CreditRequest(BaseModel):
    callback_url = TextField()
    created_at = DateTimeField()
    credit = DecimalField(null=True)
    credit_expiration = DateTimeField(null=True)
    local_number = ForeignKeyField(rel_model=LocalNumber, db_column='local_number_id', related_name='credit_requests')
    status = IntegerField()
    status_updated_at = DateTimeField()

    class Meta:
        db_table = 'credit_requests'

class Sms(BaseModel):
    created_at = DateTimeField()
    direction = IntegerField()
    external_number = CharField(max_length=15)
    local_number = ForeignKeyField(rel_model=LocalNumber, db_column='local_number_id', related_name='smss')
    message = CharField(max_length=255)
    status = IntegerField()

    class Meta:
        db_table = 'sms'

class CreditRequestMgr:
    @staticmethod
    def create_credit_request(number, callback):
        try:
            local_number = LocalNumber.select().where(LocalNumber.number == number).get()
        except LocalNumber.DoesNotExist:
            raise InvalidLocalNumberError

        credit_request = CreditRequest.create(local_number=local_number, callback_url=callback, created_at=datetime.now(), status=CREATED, status_updated_at=datetime.now())
        return credit_request.id

    @staticmethod
    def send_credit_request(number, tn):
        for credit_request in number.credit_requests.select().where(CreditRequest.status == CREATED):
            #send the select envelope message.
            tn.write('AT+STKENV="D306820181900102"\r\n')
            res = tn.read_until('"\r\n', 5)

            if len(res) < 267:
                #based on trial/error, I know that if I get a shorter string I need to just select an item of the menu and just restart the ATK message session
                tn.write('AT+STKTR="810301240082028281830100900108"\r\n')
                res = tn.read_until('"\r\n', 5)

                #restart
                tn.write('AT+STKENV="D306820181900102"\r\n')
                res = tn.read_until('"\r\n', 5)

            #first menu selection
            tn.write('AT+STKTR="810301240082028281830100900108"\r\n')
            res = tn.read_until('"\r\n', 5)

            if "STKPCI: 0" in res:
                #first selection done, select the 2nd step
                tn.write('AT+STKTR="810301240082028281830100900102"\r\n')
                res = tn.read_until('"\r\n', 5)

                if "STKPCI: 1" in res:
                    #ready to send that sms!
                    tn.write('AT+STKSMS=0\r\n')
                    #if it got this far, it's going to probably be ok... so, I'm giving it a few more seconds
                    res = tn.read_until('+STKPCI: 2\r\n', 20)

                    if "+STKPCI: 2" in res:
                        #request sent!
                        credit_request.status = CREDIT_REQUEST_SENT
                        credit_request.status_updated_at = datetime.now()
                        credit_request.save()
                        continue

            #something failed
            credit_request.status = FAILED
            credit_request.status_updated_at = datetime.now()
            credit_request.save()

    @staticmethod
    def process_credit_response(number, text):
        query = number.credit_requests.order_by(CreditRequest.created_at).where(CreditRequest.status == CREDIT_REQUEST_SENT).limit(1)
        if query.count() > 0:
            #parse message
            pending_request = query.first()
            parts = text.split(',')
            amount = float(parts[0].split('\x02')[1])
            parts = parts[1].split()
            exp_date = parts[len(parts)-1][:-1]

            #convert it to datetime
            exp_date = datetime.strptime(exp_date, "%d/%m/%Y")

            #send it to the callback
            headers = {'content-type': 'application/json'}
            payload = {
                'credit_request_id': pending_request.id, 
                'credit': amount,
                'credit_expiration': exp_date.strftime("%Y-%m-%d 23:59:59")
                }
            response = requests.post(pending_request.callback_url, data=json.dumps(payload), headers=headers)
            if response.status_code == 200:
                pending_request.status = PROCESSED
            else:
                pending_request.status = FAILED

            #update the db
            pending_request.credit = amount
            pending_request.credit_expiration = exp_date
            pending_request.status_updated_at = datetime.now()
            pending_request.save()

class SmsMgr:
    @staticmethod
    def add_message(local_number, external_number, body, direction):
        try:
            local_number = LocalNumber.select().where(LocalNumber.number == local_number).get()
        except LocalNumber.DoesNotExist:
            raise InvalidLocalNumberError

        sms = Sms.create(local_number=local_number, created_at=datetime.now(), status=CREATED, external_number=external_number, message=body, direction=direction)
        return sms.id

    @staticmethod
    def set_modes(tn):
        tn.write('AT+CSCS="GSM"\r\n')
        res = tn.read_until("\r\n", 5)
        tn.write('AT+CMGF=1\r\n')
        res = tn.read_until("\r\n", 5)
        tn.write("AT+CSMP=17,71,0,0\r\n")
        res = tn.read_until("\r\n", 5)
        tn.write("at+cmgf=1\r\n")
        res = tn.read_until("\r\n", 5)

    @staticmethod
    def process_incoming_messages(tn, text, local_number):
        #trim the text, split it and put it in tuples
        #the first element is the metadata and the second one is the data
        lines = text[:-3].rstrip().split('\r\n')

        for i in range(len(lines)//2):
            message = lines[i*2+1]
            #parse the metadata
            metadata = lines[i*2].replace('"','').split(',')

            #try to decode the message... if it fails, it doesn't need decoding
            try:
                text = binascii.unhexlify(message).decode("utf-16-be")
            except binascii.Error:
                text = message

            #don't want to pass accented values around
            text = unidecode(text)

            number_from = metadata[2]
            if number_from == "+123":
                CreditRequestMgr.process_credit_response(local_number, text)
            else:
                SmsMgr.add_message(local_number.number, number_from, text, INCOMING)

            #delete it
            msg_id = metadata[0].split()[-1]
            tn.write("at+cmgd=%s\r\n" % msg_id)
            res = tn.read_until("0\r\n", 5)

    @staticmethod
    def process_messages():
        #mark the credit requests that have been waiting for 5 minutes or more as timed out
        limit = datetime.now() - timedelta(minutes = 5)
        expire_query = CreditRequest.update(status = TIMED_OUT).where((CreditRequest.status == CREATED) & (CreditRequest.status_updated_at < limit))
        expire_query.execute()

        for server in ATServer.select():
            #start telnet connection
            try:
                tn = TelnetClient(server.ip, server.port)
                res = tn.read_until("username: ", 5)
                tn.write(server.username + "\r\n")
                res = tn.read_until("password: ", 5)
                tn.write(server.password + "\r\n")
                res = tn.read_until("]", 5)

                #retrieve SMS and send credit requests
                for number in server.local_numbers:
                    tn.write("module%i" % number.module + "\r\n")
                    res = tn.read_until("to release module %i." % number.module, 5)

                    SmsMgr.set_modes(tn)

                    tn.write('AT+CMGL="ALL"\r\n')
                    res = tn.read_until("0\r\n", 5)

                    #receive messages
                    res = tn.read_until("0\r\n", 5)
                    if res != '0\r\n': SmsMgr.process_incoming_messages(tn, res, number)

                    #handle credit requests
                    CreditRequestMgr.send_credit_request(number, tn)

                    #release module, send ctrl+x
                    tn.write(chr(24))
                    res = tn.read_until("]", 5)

                #Send the SMSs on the queue
                for sms in Sms.select().join(LocalNumber).join(ATServer).where((ATServer.id == server.id) & (Sms.status == CREATED) & (Sms.direction == OUTGOING)):
                    #select module
                    tn.write("module%i\r\n" % sms.local_number.module)
                    res = tn.read_until("module %i.\r\n" % sms.local_number.module, 5)

                    SmsMgr.set_modes(tn)

                    #set destination
                    tn.write('at+cmgs="%s"\r\n' % sms.external_number)
                    res = tn.read_until("> ", 5)

                    #send the message terminated with ctrl+z
                    tn.write(unidecode(sms.message)+chr(26))

                    #TODO: Check if it was successfully sent

                    #release module, send ctrl+x
                    tn.write(chr(24))
                    res = tn.read_until("]", 5)

                    sms.status = PROCESSED
                    sms.save()

                tn.close()

            except ConnectionRefusedError:
                print("%s - Could not connect to server %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), server.ip))

        #send the incoming SMS to the RESTful endpoint
        for sms in Sms.select().where((Sms.status == CREATED) & (Sms.direction == INCOMING)):
            headers = {'content-type': 'application/json'}
            payload = {
                'token': settings['incoming_endpoint']['token'], 
                'from': sms.external_number, 
                'to': sms.local_number.number, 
                'message': sms.message
                }
            response = requests.post(settings['incoming_endpoint']['url'], data=json.dumps(payload), headers=headers)
            if response.status_code == 200:
                sms.status = PROCESSED
            else:
                sms.status = FAILED

            sms.save()

#Exceptions
class InvalidLocalNumberError(Exception): pass