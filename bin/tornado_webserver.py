import tornado.ioloop
import tornado.web
import tornado.httpclient
import os
from influxdb_common import parse_influx
import json
import random

"""Configured via environment variables:
    
    INFLUX_PORT: Port to run the webserver on
    SPLUNK_URL: URL to Splunk's HTTP Event Collector (http://host:port/services/collector)
    SPLUNK_URLS: (Optional) Overrides SPLUNK_URL, takes a JSON formatted list of urls for Splunk hosts which will be load balanced across.
    SPLUNK_TOKEN: Auth token for Splunk's HTTP Event Collector
    SPLUNK_INDEX: Index to send Splunk Events
    SPLUNK_SOURCETYPE: Sourcetype for Splunk Events"""
    

class WriteHandler(tornado.web.RequestHandler):
    @tornado.web.asynchronous
    def post(self):
        out = parse_influx(self.request.body)
        sendstr = ""
        for x in out:
            send = { }
            send['index'] = SPLUNK_INDEX
            send['sourcetype'] = SPLUNK_SOURCETYPE
            send['time'] = x['timestamp']
            send['event'] = x
            line = json.dumps(send)
            sendstr += line
        http = tornado.httpclient.AsyncHTTPClient()
        
        if 'SPLUNK_URLS' in globals():
            url = random.choice(SPLUNK_URLS)
        else:
            url = SPLUNK_URL
            
        http.fetch(url, headers={ 'Authorization': 'Splunk %s' % SPLUNK_TOKEN },
                   method="POST", body=sendstr, callback=self.on_response, validate_cert=False)

    def on_response(self, response):
        if response.error: raise tornado.web.HTTPError(500)
        self.set_status(204, "No Content")
        self.finish()

class QueryHandler(tornado.web.RequestHandler):
    def get(self):
        self.write({ 'results': [ ] })

def make_app():
    return tornado.web.Application([
        (r"/write", WriteHandler),
        (r"/query", QueryHandler)
    ])

if __name__ == "__main__":
    # Check for valid config settings
    port = 8086 if 'PORT' not in os.environ else os.environ['INFLUX_PORT']
    globals()['SPLUNK_INDEX'] = "metrics" if 'SPLUNK_INDEX' not in os.environ else os.environ['SPLUNK_INDEX']
    globals()['SPLUNK_SOURCETYPE'] = "metrics" if 'SPLUNK_SOURCETYPE' not in os.environ else os.environ['SPLUNK_SOURCETYPE']
        
    if 'SPLUNK_URLS' in os.environ:
        globals()['SPLUNK_URLS'] = tornado.escape.json_decode(os.environ['SPLUNK_URLS'])
    elif 'SPLUNK_URL' in os.environ:
        globals()['SPLUNK_URL'] = os.environ['SPLUNK_URL']
    else:
        print 'Cannot determine Splunk URL'
        exit(1)
        
    if 'SPLUNK_TOKEN' not in os.environ:
        print 'Cannot determine Splunk Token'
        exit(1)
    else:
        globals()['SPLUNK_TOKEN'] = os.environ['SPLUNK_TOKEN']
    
    app = make_app()
    app.listen(port)
    tornado.ioloop.IOLoop.current().start()