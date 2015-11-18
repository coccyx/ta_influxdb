#CORE PYTHON IMPORTS
from __future__ import division
import sys
import os
import json
import socket
import signal
import logging, logging.handlers
from Cookie import SimpleCookie
from influxdb_common import parse_influx
import time, datetime

#CORE SPLUNK IMPORTS
#import splunk
from cherrypy import wsgiserver
from splunk import getDefault
from splunk.appserver.mrsparkle.lib.util import splunk_to_cherry_cfg, make_splunkhome_path

def setupLogger(logger=None, log_format='%(asctime)s %(levelname)s [TAOntapService] %(message)s', level=logging.INFO, log_name="ta_ontap_cred_service.log", logger_name="ta_ontap_cred_service"):
    """
    Setup a logger suitable for splunkd consumption
    """
    if logger is None:
        logger = logging.getLogger(logger_name)

    logger.propagate = False # Prevent the log messages from being duplicated in the python.log file
    logger.setLevel(level)

    file_handler = logging.handlers.RotatingFileHandler(make_splunkhome_path(['var', 'log', 'splunk', log_name]), maxBytes=2500000, backupCount=5)
    formatter = logging.Formatter(log_format)
    file_handler.setFormatter(formatter)

    logger.handlers = []
    logger.addHandler(file_handler)

    logger.debug("init %s logger", logger_name)
    return logger


#Decorators
class HandleRequest(object):
    """
    decorator for exception handling, validating and logging requests properly
    """
    def __init__(self, expected_methods, enforce_auth=True):
        """
        Request validation utility for expected HTTP verbs
        ARGS:
            expected_methods - array of supported methods
        """
        self.expected_methods = expected_methods
        self.enforce_auth = enforce_auth

    def __call__(self, fn):
        def wrapped_fn(environ, start_response):
            start = time.time()
            #Access logging through start response calls
            def wrapped_start_response(status, response_headers):
                end = time.time()
                duration = int((end - start) * 100)
                access_logger.info("%s %s '%s' - - - %sms", environ["REQUEST_METHOD"], environ.get("SCRIPT_NAME", "/"), status, duration)
                return start_response(status, response_headers)

            # print "Trying to send shit"
            try:
                return fn(environ, wrapped_start_response)
            except Exception as e:
                service_logger.exception("Internal Server Error on request='%s %s' specific error: %s", environ["REQUEST_METHOD"], environ.get("SCRIPT_NAME", "/"), str(e))
                # print e
                status = "500 Internal Server Error"
                response_headers = [('Content-type','text/plain')]
                wrapped_start_response(status, response_headers)
                return []
        return wrapped_fn


#===============================================================================
# Utilities & Globals
#===============================================================================
logname = "InfluxImpersonator_gateway.log"
service_logger = setupLogger(logger=None, log_format='%(asctime)s %(levelname)s [InfluxImpersonatorWSGI:%(process)d] %(message)s', level=logging.INFO, log_name=logname, logger_name="InfluxImpersonator-gateway")
logname = "InfluxImpersonator_access.log"
access_logger = setupLogger(logger=None, log_format='%(asctime)s %(levelname)s %(message)s', level=logging.INFO, log_name=logname, logger_name="InfluxImpersonator-access")            
        
    

#===============================================================================
# Influx Impersonator Services
#===============================================================================
@HandleRequest(["POST"])
def handle_write(environ, start_response):
    '''
    Interpret a write request in InfluxDB's POST format, documented here:
        https://influxdb.com/docs/v0.9/write_protocols/line.html
        
    Example POSTS:
        cpu,host=server01,region=uswest value=1 1434055562000000000
        cpu,host=server02,region=uswest value=3 1434055562000010000
        temperature,machine=unit42,type=assembly internal=32,external=100 1434055562000000035
        temperature,machine=unit143,type=assembly internal=22,external=130 1434055562005000035
    '''
    
    response_headers = [('Content-type','text/plain')]
    try:
        service_logger.debug("in handle_write session=%s", environ)
        
        req_in = environ.get("wsgi.input", None)
        content = req_in.read(int(environ["CONTENT_LENGTH"]))
        
        ret = parse_influx(content)
        write_events_callback(ret)
            
        status = '204 No Content'
    except Exception as e:
        service_logger.error("Received error '%s'" % (str(e)))
        status = '400 Bad Request'
    
    start_response(status, response_headers)    
    return ''
    
def write_events(events):
    '''
    Simple stdout implementation of an event writer
    '''
    for event in events:
        print json.dumps(event)
        
def handle_query(environ, start_response):
    '''
    Blindly return 200 OK to all queries with no data
    '''
    status = '200 OK'
    start_response(status, [ ])
    results = { 'results': [ ] }
    return json.dumps(results)

#===============================================================================
# Test Services
#===============================================================================
@HandleRequest(["GET"])
def test_static(environ, start_response):
    '''
    Simple static resource
    '''
    service_logger.debug("in test static environ=%s", environ)
    status = '200 OK'
    response_headers = [('Content-type','text/plain')]
    start_response(status, response_headers)
    return ['\n', 'Hail Medusa!', '\n']

@HandleRequest(["POST"])
def test_echo(environ, start_response):
    '''
    Simple, non-streaming echo server
    '''
    service_logger.debug("in test echo environ=%s", environ)
    status = '200 OK'
    req_in = environ.get("wsgi.input", None)
    content = req_in.read(int(environ["CONTENT_LENGTH"]))
    response_headers = [('Content-type','text/plain')]
    start_response(status, response_headers)
    return ["\n###########\nECHO SERVER\n###########\n", content, "\n\n"]

#===============================================================================
# Web Service Constructor
#===============================================================================

def bootstrap_web_service(port=8086, callback=write_events, service_log_level="DEBUG", access_log_level="DEBUG"):
    """
    Start up the InfluxImpersonator web service from conf file defitions

    RETURNS reference to unstarted server
    """
    # print "bootstrapping"
    #Establish the route dispatcher
    routes = {
			'/write': handle_write,
            '/query': handle_query,
            '/test/static': test_static,
            '/test/echo': test_echo }
    dispatch = wsgiserver.WSGIPathInfoDispatcher(routes)

    #Set log levels
    service_log_level = service_log_level.upper()
    if service_log_level not in ["DEBUG", "INFO", "WARN","WARNING", "ERROR"]:
        service_logger.setLevel(logging.INFO)
        service_logger.warning("unrecognizable configured service log level: %s, resetting log level to INFO", service_log_level)
    else:
        service_logger.setLevel(service_log_level)
    access_log_level = access_log_level.upper()
    if access_log_level not in ["DEBUG", "INFO", "WARN","WARNING", "ERROR"]:
        access_logger.setLevel(logging.INFO)
        access_logger.warning("unrecognizable configured access log level: %s, resetting log level to INFO", access_log_level)
    else:
        access_logger.setLevel(access_log_level)

    #Get basic configuration
    global_cfg = splunk_to_cherry_cfg('web', 'settings')
    host_name = getDefault("host")

    #Get SSL configuration
    service_logger.info('parsing SSL config from splunk web.conf...')
    priv_key_path = str(global_cfg['privKeyPath'])
    ssl_certificate = str(global_cfg['caCertPath'])
    if os.path.isabs(priv_key_path):
        global_cfg['server.ssl_private_key'] = priv_key_path
    else:
        global_cfg['server.ssl_private_key'] = make_splunkhome_path([priv_key_path])
    if os.path.isabs(ssl_certificate):
        global_cfg['server.ssl_certificate'] = ssl_certificate
    else:
        global_cfg['server.ssl_certificate'] = make_splunkhome_path([ssl_certificate])

    #Validate Configuration
    if not os.path.exists(global_cfg['server.ssl_private_key']):
        service_logger.error("Failed to bootstrap InfluxImpersonator service due to configured ssl key missing: %s", global_cfg['server.ssl_private_key'])
        raise ValueError("Private Key: '%s' Not Found" % global_cfg['server.ssl_private_key'])
    if not os.path.exists(global_cfg['server.ssl_certificate']):
        service_logger.error("Failed to bootstrap InfluxImpersonator service due to configured ssl cert missing: %s", global_cfg['server.ssl_certificate'])
        raise ValueError("Certificate: '%s' Not Found" % global_cfg['server.ssl_certificate'])

    #Validate port availability, since we can't start the server and then write the key files we need to validate prior to start
    try:
        sock = socket.socket()
        sock.connect((host_name, port))
        sock.close()
        service_logger.error("could not bootstrap gateway at %s:%s, port in use", host_name, port)
        sys.exit(1)
    except socket.error as e:
        if e.errno != 61:
            service_logger.warning("got unexpected socket error when checking port availability, will attempt to bootstrap gateway anyway. socket error: %s", str(e))

    #Build server
    server = wsgiserver.CherryPyWSGIServer(('0.0.0.0', port), dispatch, server_name=host_name)
    # server.ssl_version = 3
    # server.ssl_options = 0
    # server.ssl_certificate = global_cfg['server.ssl_certificate']
    # server.ssl_private_key = global_cfg['server.ssl_private_key']

    # print "started wsgi server %s" % host_name

    #Bind a cache serialization to SIGTERM and SIGINT
    def signal_handler(sig, frame):
        """
        Handle signals and terminate the process while preserving state.
        """
        service_logger.info("initiating safe shutdown after receiving signal=%s", sig)
        service_logger.info("stopping cherrypy wsgi server")
        server.stop()
        service_logger.info("cherrypy wsgi server stopped")
        service_logger.info("exiting parent process")
        sys.exit(0)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Establish a global for write_events_callback
    globals()['write_events_callback'] = callback
    return server

#Start the service if just running the script
if __name__ == "__main__":
    try:
        server = bootstrap_web_service(service_log_level="INFO", access_log_level="INFO")
        server.start()
    except ValueError as e:
        service_logger.exception("Failed to bootstrap InfluxImpersonator service due to configuration error: %s", str(e))
