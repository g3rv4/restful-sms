from models import SmsMgr
import time

while True:
    SmsMgr.process_messages()
    time.sleep(30)