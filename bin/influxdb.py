import random, sys
import json

from splunklib.modularinput import *
from cherrypy_webserver import bootstrap_web_service

class MyScript(Script):
    def get_scheme(self):
        scheme = Scheme("InfluxDB Input")

        scheme.description = "Run a WebServer which receives requests to InfluxDB's /write endpoint and sends them to Splunk."
        scheme.use_external_validation = True
        scheme.use_single_instance = False
        
        port_argument = Argument("port")
        port_argument.title = "Web Server Port"
        port_argument.data_type = Argument.data_type_number
        port_argument.description = "Port to access HTTP requests on to the /write endpoint"
        port_argument.required_on_create = True
        scheme.add_argument(port_argument)

        return scheme

    def stream_events(self, inputs, ew):
        # Support only one input per use_single_instance
        (input_name, input_item) = inputs.inputs.items()[0]
        self.name = input_name
        self.port = int(input_item["port"])
        self.index = "main" if input_item["index"] == "default" else input_item["index"]
        self.sourcetype = "influxdb" if "sourcetype" not in input_item else input_item["sourcetype"]
            
        self.ew = ew
        
        server = bootstrap_web_service(self.port, self.write_events, service_log_level="INFO", access_log_level="INFO")
        server.start()
        
        
    def write_events(self, events):
        for x in events:
            event = Event()
            
            event.stanza = self.name
            event.index = self.index
            event.sourcetype = self.sourcetype
            event.time = float(x['timestamp'])
            event.data = json.dumps(x)

            self.ew.write_event(event)

if __name__ == "__main__":
    sys.exit(MyScript().run(sys.argv))