from flask import Flask
from flask_restful import reqparse, abort, Api, Resource
from werkzeug.contrib.fixers import ProxyFix
from config import settings
from models import *

app = Flask(__name__)

#this is going to make it a whole lot easier to debug
app.debug = True

api = Api(app)

def abort_if_token_invalid(token):
    if token != settings['token']:
        abort(401, message="Invalid token")

def abort_if_required_params_not_present(required):
    global parser
    args = parser.parse_args()
    
    #if there's a None in any of the required values
    if any(v == None for v in [args[key] for key in required]):
        abort(400, message="The following parameter(s) are required: %s" % ', '.join([v for v in required if args[v] == None]))


parser = reqparse.RequestParser()
parser.add_argument('from', type=str)
parser.add_argument('to', type=str)
parser.add_argument('body', type=str)
parser.add_argument('number', type=str)
parser.add_argument('callback_url', type=str)

class Message(Resource):
    def post(self, token):
        abort_if_token_invalid(token)

        required_arguments = ['from', 'to', 'body']
        abort_if_required_params_not_present(required_arguments)

        args = parser.parse_args()
        return {'id': SmsMgr.add_message(args['from'], args['to'], args['body'], OUTGOING)}

class CreditRequest(Resource):
    def post(self, token):
        abort_if_token_invalid(token)

        required_arguments = ['number', 'callback_url']
        abort_if_required_params_not_present(required_arguments)

        args = parser.parse_args()
        return {'id': CreditRequestMgr.create_credit_request(args['number'], args['callback_url'])}

##
## Actually setup the Api resource routing here
##
api.add_resource(Message, '/message/<string:token>')
api.add_resource(CreditRequest, '/credit-request/<string:token>')

app.wsgi_app = ProxyFix(app.wsgi_app)
if __name__ == '__main__':
    app.run(debug=True)