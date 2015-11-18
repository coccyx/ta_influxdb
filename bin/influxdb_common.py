from __future__ import division
import json
import time
from math import log10
       
def _remove_escapes(s):
    '''
    Since escapes can be contained natively, unescaped themselves, simply removing \'s wont work.  Need to see in what context they exist and then trim them.
    '''
    breakers = [ ]
    
    for x in xrange(0, len(s)):
        c = s[x:x+1]
        next_c = s[x+1:x+2]
        
        if c == '\\' and next_c in (' ', ',', '"', '='):
            breakers.append(x)
            
    breakers.append(len(s))

    news = ''
    lastbreaker = 0
    for breaker in breakers:
        news += s[lastbreaker:breaker]
        lastbreaker = breaker+1
        
    return news
     
def _segment_influx_event(content):
    '''
    Break influx event into three component parts: Keys & Tags, Measurements, and Timestamp
    '''
    content = unicode(content)
    col = 0
    breaker = [ 0, 0 ]
    quotes = 0
    # Step through the string
    for x in xrange(0, len(content)):
        c = content[x:x+1]
        prev_c = content[x-1:x]
        # Ensure we haven't found both breakers
        if col < 2:
            # Check if we're inside a quoted string
            if c == '"':
                # Check if immediately preceding character is an escape
                if prev_c != '\\':
                    # Flip the bit back
                    if quotes == 0:
                        quotes = 1
                    else:
                        quotes = 0
            # Check for a breaker, space and not escaped
            if c == " " and not quotes and prev_c != '\\':
                breaker[col] = x
                col += 1

    # Do we have a timestamp?
    if breaker[1] != 0:
        timestamp = long(content[breaker[1]:])
    else:
        # Without a timestamp, use current time
        timestamp = long(time.time()*1000000)
        # Set second breaker to end of the string
        breaker[1] = len(content)
    measurements = content[breaker[0]:breaker[1]].strip()
    keys = content[:breaker[0]].strip()
    
    return (keys, measurements, timestamp)
    
def _find_comma_breakers(s):
    '''
    Find unescaped commas in a string of keys or measurements
    '''
    breakers = [ ]
    quotes = 0
    for x in xrange(0, len(s)):
        c = s[x:x+1]
        prev_c = s[x-1:x]
        next_c = s[x+1:x+2]
       
        if c == '"':
            # Check if immediately preceding character is an escape
            if prev_c != '\\':
                # Flip the bit back
                if quotes == 0:
                    quotes = 1
                else:
                    quotes = 0
        if c == ',' and not quotes and prev_c != '\\':
            breakers.append(x)
    
    # Append a end of string breaker
    breakers.append(len(s))
    
    return breakers
    
def _parse_influx_kv(kv, breakers, escapeequals=True):
    '''
    Iterate through a list of key=value,key2=value2 strings and return dictionary
    '''
    lastbreaker = 0
    ret = { }
    
    # Iterate through breakers, finding and spliting key value pairs
    for x in breakers:
        s = kv[lastbreaker:x]
        for y in xrange(0, len(s)):
            c = s[y:y+1]
            prev_c = s[y-1:y]
            
            if (c == '=' and prev_c != '\\' and escapeequals) \
                    or (c == '=' and not escapeequals):
                k = _remove_escapes(s[:y])
                v = _remove_escapes(s[y+1:])
                ret[k] = v
                
                lastbreaker = x+1               
    return ret
    
def _parse_influx_keys(keys):
    '''
    Parse influx Keys format: name,tag=value,tag2=value2
    '''
    name = ''
    tags = { }
    
    breakers = _find_comma_breakers(keys)
    
    # Name is always the first element
    breaker = breakers.pop(0)
    name = keys[0:breaker]
    
    # Trim off the name before sending to parse kv
    keys = keys[breaker+1:]
    breakers = _find_comma_breakers(keys)

    tags = _parse_influx_kv(keys, breakers)
    
    return (name, tags)

def _parse_influx_measurements(measurements, name):
    '''
    Parse influx measurements format: tag="string value",value=0.0
    '''
    breakers = _find_comma_breakers(measurements)
    temp = _parse_influx_kv(measurements, breakers, False)
    
    # print "breakers=%s temp=%s" % (breakers, temp)
    
    out = { }
    for (k, v) in temp.items():
        if k == 'value':
            k = name
        else:
            k = name+'.'+k
        try:    
            # If we're a string, we're enclosed in quotes
            if v[0:1] == '"' and v[-1:] == '"':
                v = v[1:-1]
            # Check if we're boolean
            elif v in ('t', 'T', 'true', 'True', 'TRUE'):
                v = True
            elif v in ('f', 'F', 'false', 'False', 'FALSE'):
                v = False
            # If the last character is an 'i', we're an integer
            elif v[-1:] == 'i':
                v = long(v[:-1])
            # Check if the last character is 'l', trim it if so
            elif v[-1:] == 'l':
                v = float(v[:-1])
            # Otherwise, we're a float
            else:
                v = float(v)
                
            out[k] = v
        except ValueError as e:
            pass
            
    return out

def parse_influx_event(content):
    '''
    Parse Influx's line protocol from https://influxdb.com/docs/v0.9/write_protocols/line.html
    '''
    (keys, measurements, timestamp) = _segment_influx_event(content)
    
    (name, tags) = _parse_influx_keys(keys)
    
    measures = _parse_influx_measurements(measurements, name)
    
    # print "keys=%s measurements=%s timestamp=%s" % (keys, measurements, timestamp)
    # print "name=%s tags=%s" % (name, tags)
    # print "measures=%s" % measures
    
    if measures:
        out = { }
        # Determine precision of timestamp and put in floating point notation
        if int(log10(timestamp))+1 == 19:
            out['timestamp'] = round(timestamp/10**9, 6)
        elif int(log10(timestamp))+1 == 16:
            out['timestamp'] = round(timestamp/10**6, 6)
        else:
            out['timestamp'] = timestamp
        out.update(measures)
        if tags:
            out['tags'] = tags
    
        return out
    else:
        return False
    
    
def parse_influx(content):
    '''
    Parse a blob of Influx's line protocol from https://influxdb.com/docs/v0.9/write_protocols/line.html.
    This breaks things into events since we can seemingly stupidly have newlines in an event.
    '''
    
    # Break content into events, call parse_influx_event
    events = [ ]
    quotes = 0
    lastbreaker = 0
    for x in xrange(0, len(content)):
        # Check if we're inside a quoted string
        if content[x:x+1] == '"':
            # Check if immediately preceding character is an escape
            if content[(x-1):x] != '\\':
                # Flip the bit back
                if quotes == 0:
                    quotes = 1
                else:
                    quotes = 0
        
        # If we get a newline and we're not inside a quoted string, break the event
        if content[x:x+1] == '\n' and not quotes:
            events.append(content[lastbreaker:x])
            lastbreaker = x+1
    
    # Append the last or only event
    events.append(content[lastbreaker:])
    
    out = [ ]
    for event in events:
        ret = parse_influx_event(event)
        if ret:
            out.append(ret)
        
    return out

    
if __name__ == '__main__':
    # events = [ 'disk_free free_space=442221834240i,disk_type="SSD" 1435362189575692182\ndisk_free free_space=442221834240i,disk_type="SSD" 1435362189575692182\ndisk_free free_space=442221834240i,disk_type="SSD" 1435362189575692182',
    #             'disk_free free_space=442221834240i,disk_type="SSD\nSome more" 1435362189575692182\ndisk_free free_space=442221834240i,disk_type="SSD" 1435362189575692182']
    # for event in events:          
    #     parse_influx(event)
    
    # print parse_influx_event(r'"measurement\ with\ quotes",tag\ key\ with\ spaces=tag\,value\,with"commas" field_key\\\\="string field value, only \" need be quoted"')
    lines = [ r'disk_free value=442221834240i',
			r'disk_free value=442221834240i 1435362189575692182',
			r'disk_free,hostname=server01,disk_type=SSD value=442221834240i',
			r'disk_free,hostname=server01,disk_type=SSD value=442221834240i 1435362189575692182',
			r'disk_free free_space=442221834240i,disk_type="SSD" 1435362189575692182',
			r'total\ disk\ free,volumes=/net\,/home\,/ value=442221834240i 1435362189575692182',
			r'disk_free,a\=b=y\=z value=442221834240i',
			r'disk_free,path=C:\Windows value=442221834240i',
			r'disk_free value=442221834240i,working\ directories="C:\My Documents\Stuff for examples,C:\My Documents"',
			r'"measurement\ with\ quotes",tag\ key\ with\ spaces=tag\,value\,with"commas" field_key="string field value, only \" need be quoted"' ]

    for line in lines:
        print parse_influx_event(line)
    
#     content = r'''log/events,hostname=10.245.1.3,pod_id=da352869-8cc4-11e5-8fb0-08002750857f,pod_name=ubuntu,uid=33b822bd-8d61-11e5-8fb0-08002750857f value="{
#  \"metadata\": {
#   \"name\": \"ubuntu.141793e89459f2bd\",
#   \"namespace\": \"default\",
#   \"selfLink\": \"/api/v1/namespaces/default/events/ubuntu.141793e89459f2bd\",
#   \"uid\": \"33b822bd-8d61-11e5-8fb0-08002750857f\",
#   \"resourceVersion\": \"2306\",
#   \"creationTimestamp\": \"2015-11-17T19:27:12Z\",
#   \"deletionTimestamp\": \"2015-11-17T20:27:12Z\"
#  },
#  \"involvedObject\": {
#   \"kind\": \"Pod\",
#   \"namespace\": \"default\",
#   \"name\": \"ubuntu\",
#   \"uid\": \"da352869-8cc4-11e5-8fb0-08002750857f\",
#   \"apiVersion\": \"v1\",
#   \"resourceVersion\": \"563\",
#   \"fieldPath\": \"spec.containers{ubuntu}\"
#  },
#  \"reason\": \"started\",
#  \"message\": \"Started with docker id 3ab0c2ec2a52\",
#  \"source\": {
#   \"component\": \"kubelet\",
#   \"host\": \"10.245.1.3\"
#  },
#  \"firstTimestamp\": \"2015-11-17T19:27:12Z\",
#  \"lastTimestamp\": \"2015-11-17T19:27:12Z\",
#  \"count\": 1
# }" 1447788432000000000
# uptime_ms_cumulative,container_base_image=kubernetes/heapster:canary,container_name=heapster,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:heapster\,version:v6\,io.kubernetes.pod.name:kube-system/heapster-0agd6,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=316b9974-8cc4-11e5-8fb0-08002750857f,pod_name=heapster-0agd6,pod_namespace=kube-system value=68060984i 1447789050000000000
# cpu/usage_ns_cumulative,container_base_image=kubernetes/heapster:canary,container_name=heapster,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:heapster\,version:v6\,io.kubernetes.pod.name:kube-system/heapster-0agd6,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=316b9974-8cc4-11e5-8fb0-08002750857f,pod_name=heapster-0agd6,pod_namespace=kube-system value=181187053042i 1447789050000000000
# cpu/limit_gauge,container_base_image=kubernetes/heapster:canary,container_name=heapster,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:heapster\,version:v6\,io.kubernetes.pod.name:kube-system/heapster-0agd6,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=316b9974-8cc4-11e5-8fb0-08002750857f,pod_name=heapster-0agd6,pod_namespace=kube-system value=1i 1447789050000000000
# memory/usage_bytes_gauge,container_base_image=kubernetes/heapster:canary,container_name=heapster,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:heapster\,version:v6\,io.kubernetes.pod.name:kube-system/heapster-0agd6,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=316b9974-8cc4-11e5-8fb0-08002750857f,pod_name=heapster-0agd6,pod_namespace=kube-system value=44957696i 1447789050000000000
# memory/working_set_bytes_gauge,container_base_image=kubernetes/heapster:canary,container_name=heapster,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:heapster\,version:v6\,io.kubernetes.pod.name:kube-system/heapster-0agd6,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=316b9974-8cc4-11e5-8fb0-08002750857f,pod_name=heapster-0agd6,pod_namespace=kube-system value=4296704i 1447789050000000000
# memory/limit_bytes_gauge,container_base_image=kubernetes/heapster:canary,container_name=heapster,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:heapster\,version:v6\,io.kubernetes.pod.name:kube-system/heapster-0agd6,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=316b9974-8cc4-11e5-8fb0-08002750857f,pod_name=heapster-0agd6,pod_namespace=kube-system value=-1i 1447789050000000000
# memory/page_faults_cumulative,container_base_image=kubernetes/heapster:canary,container_name=heapster,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:heapster\,version:v6\,io.kubernetes.pod.name:kube-system/heapster-0agd6,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=316b9974-8cc4-11e5-8fb0-08002750857f,pod_name=heapster-0agd6,pod_namespace=kube-system value=80849i 1447789050000000000
# memory/major_page_faults_cumulative,container_base_image=kubernetes/heapster:canary,container_name=heapster,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:heapster\,version:v6\,io.kubernetes.pod.name:kube-system/heapster-0agd6,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=316b9974-8cc4-11e5-8fb0-08002750857f,pod_name=heapster-0agd6,pod_namespace=kube-system value=5747i 1447789050000000000
# uptime_ms_cumulative,container_base_image=kubernetes/heapster:canary,container_name=heapster,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:heapster\,version:v6\,io.kubernetes.pod.name:kube-system/heapster-0agd6,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=316b9974-8cc4-11e5-8fb0-08002750857f,pod_name=heapster-0agd6,pod_namespace=kube-system value=68060984i 1447789055000000000
# cpu/usage_ns_cumulative,container_base_image=kubernetes/heapster:canary,container_name=heapster,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:heapster\,version:v6\,io.kubernetes.pod.name:kube-system/heapster-0agd6,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=316b9974-8cc4-11e5-8fb0-08002750857f,pod_name=heapster-0agd6,pod_namespace=kube-system value=181224920692i 1447789055000000000
# cpu/limit_gauge,container_base_image=kubernetes/heapster:canary,container_name=heapster,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:heapster\,version:v6\,io.kubernetes.pod.name:kube-system/heapster-0agd6,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=316b9974-8cc4-11e5-8fb0-08002750857f,pod_name=heapster-0agd6,pod_namespace=kube-system value=1i 1447789055000000000
# memory/usage_bytes_gauge,container_base_image=kubernetes/heapster:canary,container_name=heapster,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:heapster\,version:v6\,io.kubernetes.pod.name:kube-system/heapster-0agd6,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=316b9974-8cc4-11e5-8fb0-08002750857f,pod_name=heapster-0agd6,pod_namespace=kube-system value=44957696i 1447789055000000000
# memory/working_set_bytes_gauge,container_base_image=kubernetes/heapster:canary,container_name=heapster,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:heapster\,version:v6\,io.kubernetes.pod.name:kube-system/heapster-0agd6,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=316b9974-8cc4-11e5-8fb0-08002750857f,pod_name=heapster-0agd6,pod_namespace=kube-system value=4296704i 1447789055000000000
# memory/limit_bytes_gauge,container_base_image=kubernetes/heapster:canary,container_name=heapster,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:heapster\,version:v6\,io.kubernetes.pod.name:kube-system/heapster-0agd6,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=316b9974-8cc4-11e5-8fb0-08002750857f,pod_name=heapster-0agd6,pod_namespace=kube-system value=-1i 1447789055000000000
# memory/page_faults_cumulative,container_base_image=kubernetes/heapster:canary,container_name=heapster,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:heapster\,version:v6\,io.kubernetes.pod.name:kube-system/heapster-0agd6,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=316b9974-8cc4-11e5-8fb0-08002750857f,pod_name=heapster-0agd6,pod_namespace=kube-system value=80849i 1447789055000000000
# memory/major_page_faults_cumulative,container_base_image=kubernetes/heapster:canary,container_name=heapster,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:heapster\,version:v6\,io.kubernetes.pod.name:kube-system/heapster-0agd6,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=316b9974-8cc4-11e5-8fb0-08002750857f,pod_name=heapster-0agd6,pod_namespace=kube-system value=5747i 1447789055000000000
# uptime_ms_cumulative,container_base_image=gcr.io/google_containers/etcd:2.0.9,container_name=etcd,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=69594188i 1447789050000000000
# cpu/usage_ns_cumulative,container_base_image=gcr.io/google_containers/etcd:2.0.9,container_name=etcd,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=50739194013i 1447789050000000000
# cpu/limit_gauge,container_base_image=gcr.io/google_containers/etcd:2.0.9,container_name=etcd,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=99i 1447789050000000000
# cpu/request_gauge,container_base_image=gcr.io/google_containers/etcd:2.0.9,container_name=etcd,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=100i 1447789050000000000
# memory/usage_bytes_gauge,container_base_image=gcr.io/google_containers/etcd:2.0.9,container_name=etcd,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=8622080i 1447789050000000000
# memory/working_set_bytes_gauge,container_base_image=gcr.io/google_containers/etcd:2.0.9,container_name=etcd,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=1064960i 1447789050000000000
# memory/limit_bytes_gauge,container_base_image=gcr.io/google_containers/etcd:2.0.9,container_name=etcd,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=52428800i 1447789050000000000
# memory/request_bytes_gauge,container_base_image=gcr.io/google_containers/etcd:2.0.9,container_name=etcd,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=52428800i 1447789050000000000
# memory/page_faults_cumulative,container_base_image=gcr.io/google_containers/etcd:2.0.9,container_name=etcd,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=7415i 1447789050000000000
# memory/major_page_faults_cumulative,container_base_image=gcr.io/google_containers/etcd:2.0.9,container_name=etcd,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=753i 1447789050000000000
# uptime_ms_cumulative,container_base_image=gcr.io/google_containers/etcd:2.0.9,container_name=etcd,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=69594188i 1447789055000000000
# cpu/usage_ns_cumulative,container_base_image=gcr.io/google_containers/etcd:2.0.9,container_name=etcd,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=50751685921i 1447789055000000000
# cpu/limit_gauge,container_base_image=gcr.io/google_containers/etcd:2.0.9,container_name=etcd,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=99i 1447789055000000000
# cpu/request_gauge,container_base_image=gcr.io/google_containers/etcd:2.0.9,container_name=etcd,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=100i 1447789055000000000
# memory/usage_bytes_gauge,container_base_image=gcr.io/google_containers/etcd:2.0.9,container_name=etcd,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=8622080i 1447789055000000000
# memory/working_set_bytes_gauge,container_base_image=gcr.io/google_containers/etcd:2.0.9,container_name=etcd,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=1064960i 1447789055000000000
# memory/limit_bytes_gauge,container_base_image=gcr.io/google_containers/etcd:2.0.9,container_name=etcd,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=52428800i 1447789055000000000
# memory/request_bytes_gauge,container_base_image=gcr.io/google_containers/etcd:2.0.9,container_name=etcd,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=52428800i 1447789055000000000
# memory/page_faults_cumulative,container_base_image=gcr.io/google_containers/etcd:2.0.9,container_name=etcd,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=7415i 1447789055000000000
# memory/major_page_faults_cumulative,container_base_image=gcr.io/google_containers/etcd:2.0.9,container_name=etcd,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=753i 1447789055000000000
# uptime_ms_cumulative,container_base_image=gcr.io/google_containers/kube2sky:1.11,container_name=kube2sky,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=69575159i 1447789050000000000
# cpu/usage_ns_cumulative,container_base_image=gcr.io/google_containers/kube2sky:1.11,container_name=kube2sky,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=3346614790i 1447789050000000000
# cpu/limit_gauge,container_base_image=gcr.io/google_containers/kube2sky:1.11,container_name=kube2sky,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=99i 1447789050000000000
# cpu/request_gauge,container_base_image=gcr.io/google_containers/kube2sky:1.11,container_name=kube2sky,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=100i 1447789050000000000
# memory/usage_bytes_gauge,container_base_image=gcr.io/google_containers/kube2sky:1.11,container_name=kube2sky,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=6795264i 1447789050000000000
# memory/working_set_bytes_gauge,container_base_image=gcr.io/google_containers/kube2sky:1.11,container_name=kube2sky,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=274432i 1447789050000000000
# memory/limit_bytes_gauge,container_base_image=gcr.io/google_containers/kube2sky:1.11,container_name=kube2sky,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=52428800i 1447789050000000000
# memory/request_bytes_gauge,container_base_image=gcr.io/google_containers/kube2sky:1.11,container_name=kube2sky,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=52428800i 1447789050000000000
# memory/page_faults_cumulative,container_base_image=gcr.io/google_containers/kube2sky:1.11,container_name=kube2sky,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=3723i 1447789050000000000
# memory/major_page_faults_cumulative,container_base_image=gcr.io/google_containers/kube2sky:1.11,container_name=kube2sky,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=612i 1447789050000000000
# uptime_ms_cumulative,container_base_image=gcr.io/google_containers/kube2sky:1.11,container_name=kube2sky,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=69575159i 1447789055000000000
# cpu/usage_ns_cumulative,container_base_image=gcr.io/google_containers/kube2sky:1.11,container_name=kube2sky,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=3347339985i 1447789055000000000
# cpu/limit_gauge,container_base_image=gcr.io/google_containers/kube2sky:1.11,container_name=kube2sky,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=99i 1447789055000000000
# cpu/request_gauge,container_base_image=gcr.io/google_containers/kube2sky:1.11,container_name=kube2sky,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=100i 1447789055000000000
# memory/usage_bytes_gauge,container_base_image=gcr.io/google_containers/kube2sky:1.11,container_name=kube2sky,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=6795264i 1447789055000000000
# memory/working_set_bytes_gauge,container_base_image=gcr.io/google_containers/kube2sky:1.11,container_name=kube2sky,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=274432i 1447789055000000000
# memory/limit_bytes_gauge,container_base_image=gcr.io/google_containers/kube2sky:1.11,container_name=kube2sky,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=52428800i 1447789055000000000
# memory/request_bytes_gauge,container_base_image=gcr.io/google_containers/kube2sky:1.11,container_name=kube2sky,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=52428800i 1447789055000000000
# memory/page_faults_cumulative,container_base_image=gcr.io/google_containers/kube2sky:1.11,container_name=kube2sky,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=3723i 1447789055000000000
# memory/major_page_faults_cumulative,container_base_image=gcr.io/google_containers/kube2sky:1.11,container_name=kube2sky,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=612i 1447789055000000000
# uptime_ms_cumulative,container_base_image=gcr.io/google_containers/skydns:2015-03-11-001,container_name=skydns,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=69566129i 1447789051000000000
# cpu/usage_ns_cumulative,container_base_image=gcr.io/google_containers/skydns:2015-03-11-001,container_name=skydns,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=18985509748i 1447789051000000000
# cpu/limit_gauge,container_base_image=gcr.io/google_containers/skydns:2015-03-11-001,container_name=skydns,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=99i 1447789051000000000
# cpu/request_gauge,container_base_image=gcr.io/google_containers/skydns:2015-03-11-001,container_name=skydns,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=100i 1447789051000000000
# memory/usage_bytes_gauge,container_base_image=gcr.io/google_containers/skydns:2015-03-11-001,container_name=skydns,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=7581696i 1447789051000000000
# memory/working_set_bytes_gauge,container_base_image=gcr.io/google_containers/skydns:2015-03-11-001,container_name=skydns,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=622592i 1447789051000000000
# memory/limit_bytes_gauge,container_base_image=gcr.io/google_containers/skydns:2015-03-11-001,container_name=skydns,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=52428800i 1447789051000000000
# memory/request_bytes_gauge,container_base_image=gcr.io/google_containers/skydns:2015-03-11-001,container_name=skydns,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=52428800i 1447789051000000000
# memory/page_faults_cumulative,container_base_image=gcr.io/google_containers/skydns:2015-03-11-001,container_name=skydns,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=3272i 1447789051000000000
# memory/major_page_faults_cumulative,container_base_image=gcr.io/google_containers/skydns:2015-03-11-001,container_name=skydns,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=552i 1447789051000000000
# uptime_ms_cumulative,container_base_image=gcr.io/google_containers/skydns:2015-03-11-001,container_name=skydns,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=69566129i 1447789056000000000
# cpu/usage_ns_cumulative,container_base_image=gcr.io/google_containers/skydns:2015-03-11-001,container_name=skydns,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=18990724716i 1447789056000000000
# cpu/limit_gauge,container_base_image=gcr.io/google_containers/skydns:2015-03-11-001,container_name=skydns,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=99i 1447789056000000000
# cpu/request_gauge,container_base_image=gcr.io/google_containers/skydns:2015-03-11-001,container_name=skydns,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=100i 1447789056000000000
# memory/usage_bytes_gauge,container_base_image=gcr.io/google_containers/skydns:2015-03-11-001,container_name=skydns,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=7581696i 1447789056000000000
# memory/working_set_bytes_gauge,container_base_image=gcr.io/google_containers/skydns:2015-03-11-001,container_name=skydns,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=622592i 1447789056000000000
# memory/limit_bytes_gauge,container_base_image=gcr.io/google_containers/skydns:2015-03-11-001,container_name=skydns,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=52428800i 1447789056000000000
# memory/request_bytes_gauge,container_base_image=gcr.io/google_containers/skydns:2015-03-11-001,container_name=skydns,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=52428800i 1447789056000000000
# memory/page_faults_cumulative,container_base_image=gcr.io/google_containers/skydns:2015-03-11-001,container_name=skydns,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=3272i 1447789056000000000
# memory/major_page_faults_cumulative,container_base_image=gcr.io/google_containers/skydns:2015-03-11-001,container_name=skydns,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=552i 1447789056000000000
# uptime_ms_cumulative,container_base_image=gcr.io/google_containers/exechealthz:1.0,container_name=healthz,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=69552947i 1447789051000000000
# cpu/usage_ns_cumulative,container_base_image=gcr.io/google_containers/exechealthz:1.0,container_name=healthz,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=21840779434i 1447789051000000000
# cpu/limit_gauge,container_base_image=gcr.io/google_containers/exechealthz:1.0,container_name=healthz,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=9i 1447789051000000000
# cpu/request_gauge,container_base_image=gcr.io/google_containers/exechealthz:1.0,container_name=healthz,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=10i 1447789051000000000
# memory/usage_bytes_gauge,container_base_image=gcr.io/google_containers/exechealthz:1.0,container_name=healthz,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=5566464i 1447789051000000000
# memory/working_set_bytes_gauge,container_base_image=gcr.io/google_containers/exechealthz:1.0,container_name=healthz,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=1556480i 1447789051000000000
# memory/limit_bytes_gauge,container_base_image=gcr.io/google_containers/exechealthz:1.0,container_name=healthz,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=20971520i 1447789051000000000
# memory/request_bytes_gauge,container_base_image=gcr.io/google_containers/exechealthz:1.0,container_name=healthz,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=20971520i 1447789051000000000
# memory/page_faults_cumulative,container_base_image=gcr.io/google_containers/exechealthz:1.0,container_name=healthz,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=1388462i 1447789051000000000
# memory/major_page_faults_cumulative,container_base_image=gcr.io/google_containers/exechealthz:1.0,container_name=healthz,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=263i 1447789051000000000
# uptime_ms_cumulative,container_base_image=gcr.io/google_containers/exechealthz:1.0,container_name=healthz,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=69552947i 1447789056000000000
# cpu/usage_ns_cumulative,container_base_image=gcr.io/google_containers/exechealthz:1.0,container_name=healthz,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=21845586487i 1447789056000000000
# cpu/limit_gauge,container_base_image=gcr.io/google_containers/exechealthz:1.0,container_name=healthz,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=9i 1447789056000000000
# cpu/request_gauge,container_base_image=gcr.io/google_containers/exechealthz:1.0,container_name=healthz,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=10i 1447789056000000000
# memory/usage_bytes_gauge,container_base_image=gcr.io/google_containers/exechealthz:1.0,container_name=healthz,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=5484544i 1447789056000000000
# memory/working_set_bytes_gauge,container_base_image=gcr.io/google_containers/exechealthz:1.0,container_name=healthz,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=1474560i 1447789056000000000
# memory/limit_bytes_gauge,container_base_image=gcr.io/google_containers/exechealthz:1.0,container_name=healthz,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=20971520i 1447789056000000000
# memory/request_bytes_gauge,container_base_image=gcr.io/google_containers/exechealthz:1.0,container_name=healthz,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=20971520i 1447789056000000000
# memory/page_faults_cumulative,container_base_image=gcr.io/google_containers/exechealthz:1.0,container_name=healthz,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=1388797i 1447789056000000000
# memory/major_page_faults_cumulative,container_base_image=gcr.io/google_containers/exechealthz:1.0,container_name=healthz,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-dns\,kubernetes.io/cluster-service:true\,version:v8\,io.kubernetes.pod.name:kube-system/kube-dns-v8-jwap0,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ead177e-8cc0-11e5-8fb0-08002750857f,pod_name=kube-dns-v8-jwap0,pod_namespace=kube-system value=263i 1447789056000000000
# uptime_ms_cumulative,container_base_image=gcr.io/google_containers/kube-ui:v1.1,container_name=kube-ui,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-ui\,kubernetes.io/cluster-service:true\,version:v1\,io.kubernetes.pod.name:kube-system/kube-ui-v1-55ldb,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ea9e4a6-8cc0-11e5-8fb0-08002750857f,pod_name=kube-ui-v1-55ldb,pod_namespace=kube-system value=69592284i 1447789056000000000
# cpu/usage_ns_cumulative,container_base_image=gcr.io/google_containers/kube-ui:v1.1,container_name=kube-ui,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-ui\,kubernetes.io/cluster-service:true\,version:v1\,io.kubernetes.pod.name:kube-system/kube-ui-v1-55ldb,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ea9e4a6-8cc0-11e5-8fb0-08002750857f,pod_name=kube-ui-v1-55ldb,pod_namespace=kube-system value=149978817i 1447789056000000000
# cpu/limit_gauge,container_base_image=gcr.io/google_containers/kube-ui:v1.1,container_name=kube-ui,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-ui\,kubernetes.io/cluster-service:true\,version:v1\,io.kubernetes.pod.name:kube-system/kube-ui-v1-55ldb,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ea9e4a6-8cc0-11e5-8fb0-08002750857f,pod_name=kube-ui-v1-55ldb,pod_namespace=kube-system value=99i 1447789056000000000
# cpu/request_gauge,container_base_image=gcr.io/google_containers/kube-ui:v1.1,container_name=kube-ui,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-ui\,kubernetes.io/cluster-service:true\,version:v1\,io.kubernetes.pod.name:kube-system/kube-ui-v1-55ldb,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ea9e4a6-8cc0-11e5-8fb0-08002750857f,pod_name=kube-ui-v1-55ldb,pod_namespace=kube-system value=100i 1447789056000000000
# memory/usage_bytes_gauge,container_base_image=gcr.io/google_containers/kube-ui:v1.1,container_name=kube-ui,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-ui\,kubernetes.io/cluster-service:true\,version:v1\,io.kubernetes.pod.name:kube-system/kube-ui-v1-55ldb,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ea9e4a6-8cc0-11e5-8fb0-08002750857f,pod_name=kube-ui-v1-55ldb,pod_namespace=kube-system value=2908160i 1447789056000000000
# memory/working_set_bytes_gauge,container_base_image=gcr.io/google_containers/kube-ui:v1.1,container_name=kube-ui,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-ui\,kubernetes.io/cluster-service:true\,version:v1\,io.kubernetes.pod.name:kube-system/kube-ui-v1-55ldb,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ea9e4a6-8cc0-11e5-8fb0-08002750857f,pod_name=kube-ui-v1-55ldb,pod_namespace=kube-system value=114688i 1447789056000000000
# memory/limit_bytes_gauge,container_base_image=gcr.io/google_containers/kube-ui:v1.1,container_name=kube-ui,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-ui\,kubernetes.io/cluster-service:true\,version:v1\,io.kubernetes.pod.name:kube-system/kube-ui-v1-55ldb,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ea9e4a6-8cc0-11e5-8fb0-08002750857f,pod_name=kube-ui-v1-55ldb,pod_namespace=kube-system value=52428800i 1447789056000000000
# memory/request_bytes_gauge,container_base_image=gcr.io/google_containers/kube-ui:v1.1,container_name=kube-ui,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-ui\,kubernetes.io/cluster-service:true\,version:v1\,io.kubernetes.pod.name:kube-system/kube-ui-v1-55ldb,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ea9e4a6-8cc0-11e5-8fb0-08002750857f,pod_name=kube-ui-v1-55ldb,pod_namespace=kube-system value=52428800i 1447789056000000000
# memory/page_faults_cumulative,container_base_image=gcr.io/google_containers/kube-ui:v1.1,container_name=kube-ui,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-ui\,kubernetes.io/cluster-service:true\,version:v1\,io.kubernetes.pod.name:kube-system/kube-ui-v1-55ldb,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ea9e4a6-8cc0-11e5-8fb0-08002750857f,pod_name=kube-ui-v1-55ldb,pod_namespace=kube-system value=1509i 1447789056000000000
# memory/major_page_faults_cumulative,container_base_image=gcr.io/google_containers/kube-ui:v1.1,container_name=kube-ui,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:kube-ui\,kubernetes.io/cluster-service:true\,version:v1\,io.kubernetes.pod.name:kube-system/kube-ui-v1-55ldb,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=4ea9e4a6-8cc0-11e5-8fb0-08002750857f,pod_name=kube-ui-v1-55ldb,pod_namespace=kube-system value=136i 1447789056000000000
# uptime_ms_cumulative,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3 value=630702i 1447789051000000000
# cpu/usage_ns_cumulative,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3 value=2246945667730i 1447789051000000000
# cpu/limit_gauge,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3 value=2000i 1447789051000000000
# memory/usage_bytes_gauge,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3 value=390225920i 1447789051000000000
# memory/working_set_bytes_gauge,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3 value=93745152i 1447789051000000000
# memory/limit_bytes_gauge,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3 value=1041907712i 1447789051000000000
# memory/page_faults_cumulative,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3 value=1696743i 1447789051000000000
# memory/major_page_faults_cumulative,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3 value=2138i 1447789051000000000
# network/rx_bytes_cumulative,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3 value=271477798i 1447789051000000000
# network/rx_errors_cumulative,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789051000000000
# network/tx_bytes_cumulative,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3 value=1150756868i 1447789051000000000
# network/tx_errors_cumulative,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789051000000000
# filesystem/usage_bytes_gauge,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3,resource_id=/dev/mapper/vg_vagrant-lv_root value=5054558208i 1447789051000000000
# filesystem/usage_bytes_gauge,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3,resource_id=/dev/sda1 value=70825984i 1447789051000000000
# filesystem/limit_bytes_gauge,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3,resource_id=/dev/mapper/vg_vagrant-lv_root value=40192565248i 1447789051000000000
# filesystem/limit_bytes_gauge,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3,resource_id=/dev/sda1 value=499355648i 1447789051000000000
# uptime_ms_cumulative,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3 value=630702i 1447789056000000000
# cpu/usage_ns_cumulative,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3 value=2247489180045i 1447789056000000000
# cpu/limit_gauge,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3 value=2000i 1447789056000000000
# memory/usage_bytes_gauge,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3 value=390475776i 1447789056000000000
# memory/working_set_bytes_gauge,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3 value=93839360i 1447789056000000000
# memory/limit_bytes_gauge,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3 value=1041907712i 1447789056000000000
# memory/page_faults_cumulative,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3 value=1696749i 1447789056000000000
# memory/major_page_faults_cumulative,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3 value=2138i 1447789056000000000
# network/rx_bytes_cumulative,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3 value=271483510i 1447789056000000000
# network/rx_errors_cumulative,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789056000000000
# network/tx_bytes_cumulative,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3 value=1150953474i 1447789056000000000
# network/tx_errors_cumulative,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789056000000000
# filesystem/usage_bytes_gauge,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3,resource_id=/dev/mapper/vg_vagrant-lv_root value=5054558208i 1447789056000000000
# filesystem/usage_bytes_gauge,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3,resource_id=/dev/sda1 value=70825984i 1447789056000000000
# filesystem/limit_bytes_gauge,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3,resource_id=/dev/mapper/vg_vagrant-lv_root value=40192565248i 1447789056000000000
# filesystem/limit_bytes_gauge,container_name=machine,host_id=10.245.1.3,hostname=10.245.1.3,resource_id=/dev/sda1 value=499355648i 1447789056000000000
# uptime_ms_cumulative,container_name=/system.slice/network.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630720i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/network.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/network.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/network.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/network.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/network.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/network.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/network.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/dev-hugepages.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=630730i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/dev-hugepages.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/dev-hugepages.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/dev-hugepages.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/dev-hugepages.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/dev-hugepages.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/dev-hugepages.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/dev-hugepages.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/dev-disk-by\x2did-dm\x2dname\x2dvg_vagrant\x2dlv_swap.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=630723i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/dev-disk-by\x2did-dm\x2dname\x2dvg_vagrant\x2dlv_swap.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/dev-disk-by\x2did-dm\x2dname\x2dvg_vagrant\x2dlv_swap.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/dev-disk-by\x2did-dm\x2dname\x2dvg_vagrant\x2dlv_swap.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/dev-disk-by\x2did-dm\x2dname\x2dvg_vagrant\x2dlv_swap.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/dev-disk-by\x2did-dm\x2dname\x2dvg_vagrant\x2dlv_swap.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/dev-disk-by\x2did-dm\x2dname\x2dvg_vagrant\x2dlv_swap.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/dev-disk-by\x2did-dm\x2dname\x2dvg_vagrant\x2dlv_swap.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/kubelet.service,host_id=10.245.1.3,hostname=10.245.1.3 value=631121i 1447789050000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/kubelet.service,host_id=10.245.1.3,hostname=10.245.1.3 value=824789349954i 1447789050000000000
# cpu/limit_gauge,container_name=/system.slice/kubelet.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789050000000000
# memory/usage_bytes_gauge,container_name=/system.slice/kubelet.service,host_id=10.245.1.3,hostname=10.245.1.3 value=68833280i 1447789050000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/kubelet.service,host_id=10.245.1.3,hostname=10.245.1.3 value=18407424i 1447789050000000000
# memory/limit_bytes_gauge,container_name=/system.slice/kubelet.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789050000000000
# memory/page_faults_cumulative,container_name=/system.slice/kubelet.service,host_id=10.245.1.3,hostname=10.245.1.3 value=3261142i 1447789050000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/kubelet.service,host_id=10.245.1.3,hostname=10.245.1.3 value=15355i 1447789050000000000
# uptime_ms_cumulative,container_name=/system.slice/kubelet.service,host_id=10.245.1.3,hostname=10.245.1.3 value=631121i 1447789055000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/kubelet.service,host_id=10.245.1.3,hostname=10.245.1.3 value=825009434291i 1447789055000000000
# cpu/limit_gauge,container_name=/system.slice/kubelet.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789055000000000
# memory/usage_bytes_gauge,container_name=/system.slice/kubelet.service,host_id=10.245.1.3,hostname=10.245.1.3 value=68968448i 1447789055000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/kubelet.service,host_id=10.245.1.3,hostname=10.245.1.3 value=18550784i 1447789055000000000
# memory/limit_bytes_gauge,container_name=/system.slice/kubelet.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789055000000000
# memory/page_faults_cumulative,container_name=/system.slice/kubelet.service,host_id=10.245.1.3,hostname=10.245.1.3 value=3271754i 1447789055000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/kubelet.service,host_id=10.245.1.3,hostname=10.245.1.3 value=15355i 1447789055000000000
# uptime_ms_cumulative,container_name=/system.slice/salt-minion.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630744i 1447789051000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/salt-minion.service,host_id=10.245.1.3,hostname=10.245.1.3 value=52025549931i 1447789051000000000
# cpu/limit_gauge,container_name=/system.slice/salt-minion.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789051000000000
# memory/usage_bytes_gauge,container_name=/system.slice/salt-minion.service,host_id=10.245.1.3,hostname=10.245.1.3 value=5455872i 1447789051000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/salt-minion.service,host_id=10.245.1.3,hostname=10.245.1.3 value=4382720i 1447789051000000000
# memory/limit_bytes_gauge,container_name=/system.slice/salt-minion.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789051000000000
# memory/page_faults_cumulative,container_name=/system.slice/salt-minion.service,host_id=10.245.1.3,hostname=10.245.1.3 value=4269515i 1447789051000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/salt-minion.service,host_id=10.245.1.3,hostname=10.245.1.3 value=3398i 1447789051000000000
# uptime_ms_cumulative,container_name=/system.slice/salt-minion.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630744i 1447789056000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/salt-minion.service,host_id=10.245.1.3,hostname=10.245.1.3 value=52047616822i 1447789056000000000
# cpu/limit_gauge,container_name=/system.slice/salt-minion.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789056000000000
# memory/usage_bytes_gauge,container_name=/system.slice/salt-minion.service,host_id=10.245.1.3,hostname=10.245.1.3 value=5455872i 1447789056000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/salt-minion.service,host_id=10.245.1.3,hostname=10.245.1.3 value=4382720i 1447789056000000000
# memory/limit_bytes_gauge,container_name=/system.slice/salt-minion.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789056000000000
# memory/page_faults_cumulative,container_name=/system.slice/salt-minion.service,host_id=10.245.1.3,hostname=10.245.1.3 value=4272524i 1447789056000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/salt-minion.service,host_id=10.245.1.3,hostname=10.245.1.3 value=3398i 1447789056000000000
# uptime_ms_cumulative,container_name=/system.slice/systemd-vconsole-setup.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630889i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/systemd-vconsole-setup.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/systemd-vconsole-setup.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/systemd-vconsole-setup.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/systemd-vconsole-setup.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/systemd-vconsole-setup.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/systemd-vconsole-setup.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/systemd-vconsole-setup.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/var-lib-kubelet-pods-4ea9e4a6\x2d8cc0\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d1d18w.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=630935i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/var-lib-kubelet-pods-4ea9e4a6\x2d8cc0\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d1d18w.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/var-lib-kubelet-pods-4ea9e4a6\x2d8cc0\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d1d18w.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/var-lib-kubelet-pods-4ea9e4a6\x2d8cc0\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d1d18w.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/var-lib-kubelet-pods-4ea9e4a6\x2d8cc0\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d1d18w.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/var-lib-kubelet-pods-4ea9e4a6\x2d8cc0\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d1d18w.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/var-lib-kubelet-pods-4ea9e4a6\x2d8cc0\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d1d18w.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/var-lib-kubelet-pods-4ea9e4a6\x2d8cc0\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d1d18w.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=630883i 1447789050000000000
# cpu/usage_ns_cumulative,container_name=/system.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=1535120029558i 1447789050000000000
# cpu/limit_gauge,container_name=/system.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789050000000000
# memory/usage_bytes_gauge,container_name=/system.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=226422784i 1447789050000000000
# memory/working_set_bytes_gauge,container_name=/system.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=39567360i 1447789050000000000
# memory/limit_bytes_gauge,container_name=/system.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789050000000000
# memory/page_faults_cumulative,container_name=/system.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=859i 1447789050000000000
# memory/major_page_faults_cumulative,container_name=/system.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789050000000000
# uptime_ms_cumulative,container_name=/system.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=630883i 1447789055000000000
# cpu/usage_ns_cumulative,container_name=/system.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=1535478153825i 1447789055000000000
# cpu/limit_gauge,container_name=/system.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789055000000000
# memory/usage_bytes_gauge,container_name=/system.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=226680832i 1447789055000000000
# memory/working_set_bytes_gauge,container_name=/system.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=39669760i 1447789055000000000
# memory/limit_bytes_gauge,container_name=/system.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789055000000000
# memory/page_faults_cumulative,container_name=/system.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=859i 1447789055000000000
# memory/major_page_faults_cumulative,container_name=/system.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789055000000000
# uptime_ms_cumulative,container_name=/system.slice/kube-proxy.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630738i 1447789052000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/kube-proxy.service,host_id=10.245.1.3,hostname=10.245.1.3 value=240499517679i 1447789052000000000
# cpu/limit_gauge,container_name=/system.slice/kube-proxy.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789052000000000
# memory/usage_bytes_gauge,container_name=/system.slice/kube-proxy.service,host_id=10.245.1.3,hostname=10.245.1.3 value=12300288i 1447789052000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/kube-proxy.service,host_id=10.245.1.3,hostname=10.245.1.3 value=3891200i 1447789052000000000
# memory/limit_bytes_gauge,container_name=/system.slice/kube-proxy.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789052000000000
# memory/page_faults_cumulative,container_name=/system.slice/kube-proxy.service,host_id=10.245.1.3,hostname=10.245.1.3 value=20570180i 1447789052000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/kube-proxy.service,host_id=10.245.1.3,hostname=10.245.1.3 value=946i 1447789052000000000
# uptime_ms_cumulative,container_name=/system.slice/kube-proxy.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630738i 1447789056000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/kube-proxy.service,host_id=10.245.1.3,hostname=10.245.1.3 value=240553154474i 1447789056000000000
# cpu/limit_gauge,container_name=/system.slice/kube-proxy.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789056000000000
# memory/usage_bytes_gauge,container_name=/system.slice/kube-proxy.service,host_id=10.245.1.3,hostname=10.245.1.3 value=12300288i 1447789056000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/kube-proxy.service,host_id=10.245.1.3,hostname=10.245.1.3 value=3891200i 1447789056000000000
# memory/limit_bytes_gauge,container_name=/system.slice/kube-proxy.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789056000000000
# memory/page_faults_cumulative,container_name=/system.slice/kube-proxy.service,host_id=10.245.1.3,hostname=10.245.1.3 value=20576192i 1447789056000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/kube-proxy.service,host_id=10.245.1.3,hostname=10.245.1.3 value=946i 1447789056000000000
# uptime_ms_cumulative,container_name=/system.slice/sys-kernel-debug.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=630933i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/sys-kernel-debug.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/sys-kernel-debug.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/sys-kernel-debug.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/sys-kernel-debug.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/sys-kernel-debug.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/sys-kernel-debug.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/sys-kernel-debug.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/lvm2-monitor.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630734i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/lvm2-monitor.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/lvm2-monitor.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/lvm2-monitor.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/lvm2-monitor.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/lvm2-monitor.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/lvm2-monitor.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/lvm2-monitor.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/dev-disk-by\x2did-dm\x2duuid\x2dLVM\x2dlDmhHt8K94WI2UkPtmyDeS7br4gcCfX6NMXhCEePUMS8vA6fq5TNZabDd2WtwvVi.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=630901i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/dev-disk-by\x2did-dm\x2duuid\x2dLVM\x2dlDmhHt8K94WI2UkPtmyDeS7br4gcCfX6NMXhCEePUMS8vA6fq5TNZabDd2WtwvVi.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/dev-disk-by\x2did-dm\x2duuid\x2dLVM\x2dlDmhHt8K94WI2UkPtmyDeS7br4gcCfX6NMXhCEePUMS8vA6fq5TNZabDd2WtwvVi.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/dev-disk-by\x2did-dm\x2duuid\x2dLVM\x2dlDmhHt8K94WI2UkPtmyDeS7br4gcCfX6NMXhCEePUMS8vA6fq5TNZabDd2WtwvVi.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/dev-disk-by\x2did-dm\x2duuid\x2dLVM\x2dlDmhHt8K94WI2UkPtmyDeS7br4gcCfX6NMXhCEePUMS8vA6fq5TNZabDd2WtwvVi.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/dev-disk-by\x2did-dm\x2duuid\x2dLVM\x2dlDmhHt8K94WI2UkPtmyDeS7br4gcCfX6NMXhCEePUMS8vA6fq5TNZabDd2WtwvVi.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/dev-disk-by\x2did-dm\x2duuid\x2dLVM\x2dlDmhHt8K94WI2UkPtmyDeS7br4gcCfX6NMXhCEePUMS8vA6fq5TNZabDd2WtwvVi.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/dev-disk-by\x2did-dm\x2duuid\x2dLVM\x2dlDmhHt8K94WI2UkPtmyDeS7br4gcCfX6NMXhCEePUMS8vA6fq5TNZabDd2WtwvVi.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/docker.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630899i 1447789058000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/docker.service,host_id=10.245.1.3,hostname=10.245.1.3 value=11014608745i 1447789058000000000
# cpu/limit_gauge,container_name=/system.slice/docker.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789058000000000
# memory/usage_bytes_gauge,container_name=/system.slice/docker.service,host_id=10.245.1.3,hostname=10.245.1.3 value=6021120i 1447789058000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/docker.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1613824i 1447789058000000000
# memory/limit_bytes_gauge,container_name=/system.slice/docker.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789058000000000
# memory/page_faults_cumulative,container_name=/system.slice/docker.service,host_id=10.245.1.3,hostname=10.245.1.3 value=37540i 1447789058000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/docker.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789058000000000
# uptime_ms_cumulative,container_name=/system.slice/systemd-logind.service,host_id=10.245.1.3,hostname=10.245.1.3 value=631140i 1447789055000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/systemd-logind.service,host_id=10.245.1.3,hostname=10.245.1.3 value=190303202i 1447789055000000000
# cpu/limit_gauge,container_name=/system.slice/systemd-logind.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789055000000000
# memory/usage_bytes_gauge,container_name=/system.slice/systemd-logind.service,host_id=10.245.1.3,hostname=10.245.1.3 value=471040i 1447789055000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/systemd-logind.service,host_id=10.245.1.3,hostname=10.245.1.3 value=28672i 1447789055000000000
# memory/limit_bytes_gauge,container_name=/system.slice/systemd-logind.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789055000000000
# memory/page_faults_cumulative,container_name=/system.slice/systemd-logind.service,host_id=10.245.1.3,hostname=10.245.1.3 value=277i 1447789055000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/systemd-logind.service,host_id=10.245.1.3,hostname=10.245.1.3 value=58i 1447789055000000000
# uptime_ms_cumulative,container_name=/system.slice/vboxadd-service.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630736i 1447789050000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/vboxadd-service.service,host_id=10.245.1.3,hostname=10.245.1.3 value=3789673982i 1447789050000000000
# cpu/limit_gauge,container_name=/system.slice/vboxadd-service.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789050000000000
# memory/usage_bytes_gauge,container_name=/system.slice/vboxadd-service.service,host_id=10.245.1.3,hostname=10.245.1.3 value=770048i 1447789050000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/vboxadd-service.service,host_id=10.245.1.3,hostname=10.245.1.3 value=24576i 1447789050000000000
# memory/limit_bytes_gauge,container_name=/system.slice/vboxadd-service.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789050000000000
# memory/page_faults_cumulative,container_name=/system.slice/vboxadd-service.service,host_id=10.245.1.3,hostname=10.245.1.3 value=254i 1447789050000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/vboxadd-service.service,host_id=10.245.1.3,hostname=10.245.1.3 value=73i 1447789050000000000
# uptime_ms_cumulative,container_name=/system.slice/vboxadd-service.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630736i 1447789055000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/vboxadd-service.service,host_id=10.245.1.3,hostname=10.245.1.3 value=3790643317i 1447789055000000000
# cpu/limit_gauge,container_name=/system.slice/vboxadd-service.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789055000000000
# memory/usage_bytes_gauge,container_name=/system.slice/vboxadd-service.service,host_id=10.245.1.3,hostname=10.245.1.3 value=770048i 1447789055000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/vboxadd-service.service,host_id=10.245.1.3,hostname=10.245.1.3 value=24576i 1447789055000000000
# memory/limit_bytes_gauge,container_name=/system.slice/vboxadd-service.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789055000000000
# memory/page_faults_cumulative,container_name=/system.slice/vboxadd-service.service,host_id=10.245.1.3,hostname=10.245.1.3 value=254i 1447789055000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/vboxadd-service.service,host_id=10.245.1.3,hostname=10.245.1.3 value=73i 1447789055000000000
# uptime_ms_cumulative,container_name=/system.slice/crond.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630717i 1447789054000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/crond.service,host_id=10.245.1.3,hostname=10.245.1.3 value=168277967i 1447789054000000000
# cpu/limit_gauge,container_name=/system.slice/crond.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789054000000000
# memory/usage_bytes_gauge,container_name=/system.slice/crond.service,host_id=10.245.1.3,hostname=10.245.1.3 value=110592i 1447789054000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/crond.service,host_id=10.245.1.3,hostname=10.245.1.3 value=24576i 1447789054000000000
# memory/limit_bytes_gauge,container_name=/system.slice/crond.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789054000000000
# memory/page_faults_cumulative,container_name=/system.slice/crond.service,host_id=10.245.1.3,hostname=10.245.1.3 value=2048i 1447789054000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/crond.service,host_id=10.245.1.3,hostname=10.245.1.3 value=75i 1447789054000000000
# uptime_ms_cumulative,container_name=/system.slice/crond.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630717i 1447789055000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/crond.service,host_id=10.245.1.3,hostname=10.245.1.3 value=168277967i 1447789055000000000
# cpu/limit_gauge,container_name=/system.slice/crond.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789055000000000
# memory/usage_bytes_gauge,container_name=/system.slice/crond.service,host_id=10.245.1.3,hostname=10.245.1.3 value=110592i 1447789055000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/crond.service,host_id=10.245.1.3,hostname=10.245.1.3 value=24576i 1447789055000000000
# memory/limit_bytes_gauge,container_name=/system.slice/crond.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789055000000000
# memory/page_faults_cumulative,container_name=/system.slice/crond.service,host_id=10.245.1.3,hostname=10.245.1.3 value=2048i 1447789055000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/crond.service,host_id=10.245.1.3,hostname=10.245.1.3 value=75i 1447789055000000000
# uptime_ms_cumulative,container_name=/system.slice/vboxadd-x11.service,host_id=10.245.1.3,hostname=10.245.1.3 value=631149i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/vboxadd-x11.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/vboxadd-x11.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/vboxadd-x11.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/vboxadd-x11.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/vboxadd-x11.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/vboxadd-x11.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/vboxadd-x11.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/systemd-random-seed.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630856i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/systemd-random-seed.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/systemd-random-seed.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/systemd-random-seed.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/systemd-random-seed.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/systemd-random-seed.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/systemd-random-seed.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/systemd-random-seed.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/NetworkManager.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630716i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/NetworkManager.service,host_id=10.245.1.3,hostname=10.245.1.3 value=271420331i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/NetworkManager.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/NetworkManager.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1363968i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/NetworkManager.service,host_id=10.245.1.3,hostname=10.245.1.3 value=45056i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/NetworkManager.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/NetworkManager.service,host_id=10.245.1.3,hostname=10.245.1.3 value=722i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/NetworkManager.service,host_id=10.245.1.3,hostname=10.245.1.3 value=197i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/-.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=630784i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/-.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/-.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/-.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/-.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/-.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/-.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/-.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/polkit.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630893i 1447789054000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/polkit.service,host_id=10.245.1.3,hostname=10.245.1.3 value=26362529i 1447789054000000000
# cpu/limit_gauge,container_name=/system.slice/polkit.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789054000000000
# memory/usage_bytes_gauge,container_name=/system.slice/polkit.service,host_id=10.245.1.3,hostname=10.245.1.3 value=16384i 1447789054000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/polkit.service,host_id=10.245.1.3,hostname=10.245.1.3 value=8192i 1447789054000000000
# memory/limit_bytes_gauge,container_name=/system.slice/polkit.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789054000000000
# memory/page_faults_cumulative,container_name=/system.slice/polkit.service,host_id=10.245.1.3,hostname=10.245.1.3 value=255i 1447789054000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/polkit.service,host_id=10.245.1.3,hostname=10.245.1.3 value=83i 1447789054000000000
# uptime_ms_cumulative,container_name=/system.slice/polkit.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630893i 1447789055000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/polkit.service,host_id=10.245.1.3,hostname=10.245.1.3 value=26362529i 1447789055000000000
# cpu/limit_gauge,container_name=/system.slice/polkit.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789055000000000
# memory/usage_bytes_gauge,container_name=/system.slice/polkit.service,host_id=10.245.1.3,hostname=10.245.1.3 value=16384i 1447789055000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/polkit.service,host_id=10.245.1.3,hostname=10.245.1.3 value=8192i 1447789055000000000
# memory/limit_bytes_gauge,container_name=/system.slice/polkit.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789055000000000
# memory/page_faults_cumulative,container_name=/system.slice/polkit.service,host_id=10.245.1.3,hostname=10.245.1.3 value=255i 1447789055000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/polkit.service,host_id=10.245.1.3,hostname=10.245.1.3 value=83i 1447789055000000000
# uptime_ms_cumulative,container_name=/system.slice/lvm2-lvmetad.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630721i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/lvm2-lvmetad.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/lvm2-lvmetad.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/lvm2-lvmetad.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/lvm2-lvmetad.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/lvm2-lvmetad.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/lvm2-lvmetad.service,host_id=10.245.1.3,hostname=10.245.1.3 value=2i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/lvm2-lvmetad.service,host_id=10.245.1.3,hostname=10.245.1.3 value=2i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/dev-mqueue.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=630932i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/dev-mqueue.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/dev-mqueue.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/dev-mqueue.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/dev-mqueue.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/dev-mqueue.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/dev-mqueue.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/dev-mqueue.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/openvswitch-nonetwork.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630723i 1447789051000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/openvswitch-nonetwork.service,host_id=10.245.1.3,hostname=10.245.1.3 value=12909907360i 1447789051000000000
# cpu/limit_gauge,container_name=/system.slice/openvswitch-nonetwork.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789051000000000
# memory/usage_bytes_gauge,container_name=/system.slice/openvswitch-nonetwork.service,host_id=10.245.1.3,hostname=10.245.1.3 value=368640i 1447789051000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/openvswitch-nonetwork.service,host_id=10.245.1.3,hostname=10.245.1.3 value=40960i 1447789051000000000
# memory/limit_bytes_gauge,container_name=/system.slice/openvswitch-nonetwork.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789051000000000
# memory/page_faults_cumulative,container_name=/system.slice/openvswitch-nonetwork.service,host_id=10.245.1.3,hostname=10.245.1.3 value=261i 1447789051000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/openvswitch-nonetwork.service,host_id=10.245.1.3,hostname=10.245.1.3 value=89i 1447789051000000000
# uptime_ms_cumulative,container_name=/system.slice/openvswitch-nonetwork.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630723i 1447789056000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/openvswitch-nonetwork.service,host_id=10.245.1.3,hostname=10.245.1.3 value=12913010840i 1447789056000000000
# cpu/limit_gauge,container_name=/system.slice/openvswitch-nonetwork.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789056000000000
# memory/usage_bytes_gauge,container_name=/system.slice/openvswitch-nonetwork.service,host_id=10.245.1.3,hostname=10.245.1.3 value=368640i 1447789056000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/openvswitch-nonetwork.service,host_id=10.245.1.3,hostname=10.245.1.3 value=40960i 1447789056000000000
# memory/limit_bytes_gauge,container_name=/system.slice/openvswitch-nonetwork.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789056000000000
# memory/page_faults_cumulative,container_name=/system.slice/openvswitch-nonetwork.service,host_id=10.245.1.3,hostname=10.245.1.3 value=261i 1447789056000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/openvswitch-nonetwork.service,host_id=10.245.1.3,hostname=10.245.1.3 value=89i 1447789056000000000
# uptime_ms_cumulative,container_name=/system.slice/system-systemd\x2dfsck.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=630937i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/system-systemd\x2dfsck.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/system-systemd\x2dfsck.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/system-systemd\x2dfsck.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/system-systemd\x2dfsck.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/system-systemd\x2dfsck.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/system-systemd\x2dfsck.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/system-systemd\x2dfsck.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/dev-disk-by\x2duuid-83efaf00\x2d0a81\x2d452d\x2d880b\x2df57556462214.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=630714i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/dev-disk-by\x2duuid-83efaf00\x2d0a81\x2d452d\x2d880b\x2df57556462214.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/dev-disk-by\x2duuid-83efaf00\x2d0a81\x2d452d\x2d880b\x2df57556462214.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/dev-disk-by\x2duuid-83efaf00\x2d0a81\x2d452d\x2d880b\x2df57556462214.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/dev-disk-by\x2duuid-83efaf00\x2d0a81\x2d452d\x2d880b\x2df57556462214.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/dev-disk-by\x2duuid-83efaf00\x2d0a81\x2d452d\x2d880b\x2df57556462214.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/dev-disk-by\x2duuid-83efaf00\x2d0a81\x2d452d\x2d880b\x2df57556462214.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/dev-disk-by\x2duuid-83efaf00\x2d0a81\x2d452d\x2d880b\x2df57556462214.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/dev-mapper-vg_vagrant\x2dlv_swap.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=630897i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/dev-mapper-vg_vagrant\x2dlv_swap.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/dev-mapper-vg_vagrant\x2dlv_swap.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/dev-mapper-vg_vagrant\x2dlv_swap.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/dev-mapper-vg_vagrant\x2dlv_swap.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/dev-mapper-vg_vagrant\x2dlv_swap.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/dev-mapper-vg_vagrant\x2dlv_swap.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/dev-mapper-vg_vagrant\x2dlv_swap.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/systemd-update-utmp.service,host_id=10.245.1.3,hostname=10.245.1.3 value=631138i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/systemd-update-utmp.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/systemd-update-utmp.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/systemd-update-utmp.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/systemd-update-utmp.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/systemd-update-utmp.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/systemd-update-utmp.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/systemd-update-utmp.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/systemd-journald.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630735i 1447789050000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/systemd-journald.service,host_id=10.245.1.3,hostname=10.245.1.3 value=89338182544i 1447789050000000000
# cpu/limit_gauge,container_name=/system.slice/systemd-journald.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789050000000000
# memory/usage_bytes_gauge,container_name=/system.slice/systemd-journald.service,host_id=10.245.1.3,hostname=10.245.1.3 value=47026176i 1447789050000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/systemd-journald.service,host_id=10.245.1.3,hostname=10.245.1.3 value=376832i 1447789050000000000
# memory/limit_bytes_gauge,container_name=/system.slice/systemd-journald.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789050000000000
# memory/page_faults_cumulative,container_name=/system.slice/systemd-journald.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1577770i 1447789050000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/systemd-journald.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1180i 1447789050000000000
# uptime_ms_cumulative,container_name=/system.slice/systemd-journald.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630735i 1447789055000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/systemd-journald.service,host_id=10.245.1.3,hostname=10.245.1.3 value=89362681335i 1447789055000000000
# cpu/limit_gauge,container_name=/system.slice/systemd-journald.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789055000000000
# memory/usage_bytes_gauge,container_name=/system.slice/systemd-journald.service,host_id=10.245.1.3,hostname=10.245.1.3 value=47173632i 1447789055000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/systemd-journald.service,host_id=10.245.1.3,hostname=10.245.1.3 value=360448i 1447789055000000000
# memory/limit_bytes_gauge,container_name=/system.slice/systemd-journald.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789055000000000
# memory/page_faults_cumulative,container_name=/system.slice/systemd-journald.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1578323i 1447789055000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/systemd-journald.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1180i 1447789055000000000
# uptime_ms_cumulative,container_name=/system.slice/systemd-journal-flush.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630885i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/systemd-journal-flush.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/systemd-journal-flush.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/systemd-journal-flush.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/systemd-journal-flush.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/systemd-journal-flush.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/systemd-journal-flush.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/systemd-journal-flush.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/systemd-journald-dev-log.socket,host_id=10.245.1.3,hostname=10.245.1.3 value=631144i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/systemd-journald-dev-log.socket,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/systemd-journald-dev-log.socket,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/systemd-journald-dev-log.socket,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/systemd-journald-dev-log.socket,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/systemd-journald-dev-log.socket,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/systemd-journald-dev-log.socket,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/systemd-journald-dev-log.socket,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/vagrant.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=630938i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/vagrant.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/vagrant.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/vagrant.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/vagrant.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/vagrant.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/vagrant.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/vagrant.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/systemd-tmpfiles-setup-dev.service,host_id=10.245.1.3,hostname=10.245.1.3 value=631032i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/systemd-tmpfiles-setup-dev.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/systemd-tmpfiles-setup-dev.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/systemd-tmpfiles-setup-dev.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/systemd-tmpfiles-setup-dev.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/systemd-tmpfiles-setup-dev.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/systemd-tmpfiles-setup-dev.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/systemd-tmpfiles-setup-dev.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=docker-daemon,host_id=10.245.1.3,hostname=10.245.1.3 value=630886i 1447789051000000000
# cpu/usage_ns_cumulative,container_name=docker-daemon,host_id=10.245.1.3,hostname=10.245.1.3 value=413608104404i 1447789051000000000
# cpu/limit_gauge,container_name=docker-daemon,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789051000000000
# memory/usage_bytes_gauge,container_name=docker-daemon,host_id=10.245.1.3,hostname=10.245.1.3 value=31420416i 1447789051000000000
# memory/working_set_bytes_gauge,container_name=docker-daemon,host_id=10.245.1.3,hostname=10.245.1.3 value=4857856i 1447789051000000000
# memory/limit_bytes_gauge,container_name=docker-daemon,host_id=10.245.1.3,hostname=10.245.1.3 value=729337856i 1447789051000000000
# memory/page_faults_cumulative,container_name=docker-daemon,host_id=10.245.1.3,hostname=10.245.1.3 value=433146i 1447789051000000000
# memory/major_page_faults_cumulative,container_name=docker-daemon,host_id=10.245.1.3,hostname=10.245.1.3 value=4835i 1447789051000000000
# uptime_ms_cumulative,container_name=docker-daemon,host_id=10.245.1.3,hostname=10.245.1.3 value=630886i 1447789056000000000
# cpu/usage_ns_cumulative,container_name=docker-daemon,host_id=10.245.1.3,hostname=10.245.1.3 value=413717654079i 1447789056000000000
# cpu/limit_gauge,container_name=docker-daemon,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789056000000000
# memory/usage_bytes_gauge,container_name=docker-daemon,host_id=10.245.1.3,hostname=10.245.1.3 value=31424512i 1447789056000000000
# memory/working_set_bytes_gauge,container_name=docker-daemon,host_id=10.245.1.3,hostname=10.245.1.3 value=4857856i 1447789056000000000
# memory/limit_bytes_gauge,container_name=docker-daemon,host_id=10.245.1.3,hostname=10.245.1.3 value=729337856i 1447789056000000000
# memory/page_faults_cumulative,container_name=docker-daemon,host_id=10.245.1.3,hostname=10.245.1.3 value=433148i 1447789056000000000
# memory/major_page_faults_cumulative,container_name=docker-daemon,host_id=10.245.1.3,hostname=10.245.1.3 value=4836i 1447789056000000000
# uptime_ms_cumulative,container_name=/system.slice/systemd-user-sessions.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630726i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/systemd-user-sessions.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/systemd-user-sessions.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/systemd-user-sessions.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/systemd-user-sessions.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/systemd-user-sessions.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/systemd-user-sessions.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/systemd-user-sessions.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/system-getty.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=630940i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/system-getty.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/system-getty.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/system-getty.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/system-getty.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/system-getty.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/system-getty.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=2i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/system-getty.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=2i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/kmod-static-nodes.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630892i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/kmod-static-nodes.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/kmod-static-nodes.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/kmod-static-nodes.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/kmod-static-nodes.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/kmod-static-nodes.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/kmod-static-nodes.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/kmod-static-nodes.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/openvswitch.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630730i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/openvswitch.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/openvswitch.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/openvswitch.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/openvswitch.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/openvswitch.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/openvswitch.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/openvswitch.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/tmp.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=630733i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/tmp.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/tmp.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/tmp.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/tmp.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/tmp.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/tmp.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/tmp.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/auditd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630732i 1447789051000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/auditd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=2378773508i 1447789051000000000
# cpu/limit_gauge,container_name=/system.slice/auditd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789051000000000
# memory/usage_bytes_gauge,container_name=/system.slice/auditd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=294912i 1447789051000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/auditd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=159744i 1447789051000000000
# memory/limit_bytes_gauge,container_name=/system.slice/auditd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789051000000000
# memory/page_faults_cumulative,container_name=/system.slice/auditd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=191i 1447789051000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/auditd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=60i 1447789051000000000
# uptime_ms_cumulative,container_name=/system.slice/auditd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630732i 1447789057000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/auditd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=2379269325i 1447789057000000000
# cpu/limit_gauge,container_name=/system.slice/auditd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789057000000000
# memory/usage_bytes_gauge,container_name=/system.slice/auditd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=294912i 1447789057000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/auditd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=159744i 1447789057000000000
# memory/limit_bytes_gauge,container_name=/system.slice/auditd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789057000000000
# memory/page_faults_cumulative,container_name=/system.slice/auditd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=191i 1447789057000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/auditd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=60i 1447789057000000000
# uptime_ms_cumulative,container_name=/system.slice/boot.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=630726i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/boot.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/boot.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/boot.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/boot.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/boot.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/boot.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/boot.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/dbus.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630717i 1447789053000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/dbus.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1019648769i 1447789053000000000
# cpu/limit_gauge,container_name=/system.slice/dbus.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789053000000000
# memory/usage_bytes_gauge,container_name=/system.slice/dbus.service,host_id=10.245.1.3,hostname=10.245.1.3 value=483328i 1447789053000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/dbus.service,host_id=10.245.1.3,hostname=10.245.1.3 value=61440i 1447789053000000000
# memory/limit_bytes_gauge,container_name=/system.slice/dbus.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789053000000000
# memory/page_faults_cumulative,container_name=/system.slice/dbus.service,host_id=10.245.1.3,hostname=10.245.1.3 value=472i 1447789053000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/dbus.service,host_id=10.245.1.3,hostname=10.245.1.3 value=139i 1447789053000000000
# uptime_ms_cumulative,container_name=/system.slice/dbus.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630717i 1447789056000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/dbus.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1019800032i 1447789056000000000
# cpu/limit_gauge,container_name=/system.slice/dbus.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789056000000000
# memory/usage_bytes_gauge,container_name=/system.slice/dbus.service,host_id=10.245.1.3,hostname=10.245.1.3 value=483328i 1447789056000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/dbus.service,host_id=10.245.1.3,hostname=10.245.1.3 value=61440i 1447789056000000000
# memory/limit_bytes_gauge,container_name=/system.slice/dbus.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789056000000000
# memory/page_faults_cumulative,container_name=/system.slice/dbus.service,host_id=10.245.1.3,hostname=10.245.1.3 value=472i 1447789056000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/dbus.service,host_id=10.245.1.3,hostname=10.245.1.3 value=139i 1447789056000000000
# uptime_ms_cumulative,container_name=/system.slice/vboxadd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630719i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/vboxadd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/vboxadd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/vboxadd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/vboxadd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/vboxadd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/vboxadd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/vboxadd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/systemd-udev-trigger.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630941i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/systemd-udev-trigger.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/systemd-udev-trigger.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/systemd-udev-trigger.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/systemd-udev-trigger.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/systemd-udev-trigger.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/systemd-udev-trigger.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/systemd-udev-trigger.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/dev-vg_vagrant-lv_swap.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=630731i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/dev-vg_vagrant-lv_swap.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/dev-vg_vagrant-lv_swap.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/dev-vg_vagrant-lv_swap.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/dev-vg_vagrant-lv_swap.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/dev-vg_vagrant-lv_swap.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/dev-vg_vagrant-lv_swap.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/dev-vg_vagrant-lv_swap.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/systemd-tmpfiles-setup.service,host_id=10.245.1.3,hostname=10.245.1.3 value=631131i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/systemd-tmpfiles-setup.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/systemd-tmpfiles-setup.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/systemd-tmpfiles-setup.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/systemd-tmpfiles-setup.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/systemd-tmpfiles-setup.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/systemd-tmpfiles-setup.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/systemd-tmpfiles-setup.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/var-lib-kubelet-pods-316b9974\x2d8cc4\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d1d18w.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=631148i 1447789054000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/var-lib-kubelet-pods-316b9974\x2d8cc4\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d1d18w.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789054000000000
# cpu/limit_gauge,container_name=/system.slice/var-lib-kubelet-pods-316b9974\x2d8cc4\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d1d18w.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789054000000000
# memory/usage_bytes_gauge,container_name=/system.slice/var-lib-kubelet-pods-316b9974\x2d8cc4\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d1d18w.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789054000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/var-lib-kubelet-pods-316b9974\x2d8cc4\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d1d18w.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789054000000000
# memory/limit_bytes_gauge,container_name=/system.slice/var-lib-kubelet-pods-316b9974\x2d8cc4\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d1d18w.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789054000000000
# memory/page_faults_cumulative,container_name=/system.slice/var-lib-kubelet-pods-316b9974\x2d8cc4\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d1d18w.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789054000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/var-lib-kubelet-pods-316b9974\x2d8cc4\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d1d18w.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789054000000000
# uptime_ms_cumulative,container_name=/system.slice/system-lvm2\x2dpvscan.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=630859i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/system-lvm2\x2dpvscan.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/system-lvm2\x2dpvscan.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/system-lvm2\x2dpvscan.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/system-lvm2\x2dpvscan.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/system-lvm2\x2dpvscan.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/system-lvm2\x2dpvscan.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/system-lvm2\x2dpvscan.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/dev-dm\x2d1.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=630720i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/dev-dm\x2d1.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/dev-dm\x2d1.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/dev-dm\x2d1.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/dev-dm\x2d1.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/dev-dm\x2d1.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/dev-dm\x2d1.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/dev-dm\x2d1.swap,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/sshd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630896i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/sshd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/sshd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/sshd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/sshd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/sshd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/sshd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=2i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/sshd.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/systemd-sysctl.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630888i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/systemd-sysctl.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/systemd-sysctl.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/systemd-sysctl.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/systemd-sysctl.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/systemd-sysctl.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/systemd-sysctl.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/systemd-sysctl.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/systemd-fsck-root.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630787i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/systemd-fsck-root.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/systemd-fsck-root.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/systemd-fsck-root.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/systemd-fsck-root.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/systemd-fsck-root.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/systemd-fsck-root.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/systemd-fsck-root.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/fedora-readonly.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630724i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/fedora-readonly.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/fedora-readonly.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/fedora-readonly.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/fedora-readonly.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/fedora-readonly.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/fedora-readonly.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/fedora-readonly.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/systemd-remount-fs.service,host_id=10.245.1.3,hostname=10.245.1.3 value=630733i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/systemd-remount-fs.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/systemd-remount-fs.service,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/systemd-remount-fs.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/systemd-remount-fs.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/systemd-remount-fs.service,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/systemd-remount-fs.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/systemd-remount-fs.service,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=kube-proxy,host_id=10.245.1.3,hostname=10.245.1.3 value=630727i 1447789054000000000
# cpu/usage_ns_cumulative,container_name=kube-proxy,host_id=10.245.1.3,hostname=10.245.1.3 value=628006928i 1447789054000000000
# cpu/limit_gauge,container_name=kube-proxy,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789054000000000
# memory/usage_bytes_gauge,container_name=kube-proxy,host_id=10.245.1.3,hostname=10.245.1.3 value=180224i 1447789054000000000
# memory/working_set_bytes_gauge,container_name=kube-proxy,host_id=10.245.1.3,hostname=10.245.1.3 value=73728i 1447789054000000000
# memory/limit_bytes_gauge,container_name=kube-proxy,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789054000000000
# memory/page_faults_cumulative,container_name=kube-proxy,host_id=10.245.1.3,hostname=10.245.1.3 value=51117i 1447789054000000000
# memory/major_page_faults_cumulative,container_name=kube-proxy,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789054000000000
# uptime_ms_cumulative,container_name=/system.slice/var-lib-kubelet-pods-4ead177e\x2d8cc0\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d1d18w.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=630932i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/var-lib-kubelet-pods-4ead177e\x2d8cc0\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d1d18w.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/var-lib-kubelet-pods-4ead177e\x2d8cc0\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d1d18w.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/var-lib-kubelet-pods-4ead177e\x2d8cc0\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d1d18w.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/var-lib-kubelet-pods-4ead177e\x2d8cc0\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d1d18w.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/var-lib-kubelet-pods-4ead177e\x2d8cc0\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d1d18w.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/var-lib-kubelet-pods-4ead177e\x2d8cc0\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d1d18w.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/var-lib-kubelet-pods-4ead177e\x2d8cc0\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d1d18w.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/sys-kernel-config.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=630893i 1447789060000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/sys-kernel-config.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# cpu/limit_gauge,container_name=/system.slice/sys-kernel-config.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789060000000000
# memory/usage_bytes_gauge,container_name=/system.slice/sys-kernel-config.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/sys-kernel-config.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/limit_bytes_gauge,container_name=/system.slice/sys-kernel-config.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789060000000000
# memory/page_faults_cumulative,container_name=/system.slice/sys-kernel-config.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/sys-kernel-config.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789060000000000
# uptime_ms_cumulative,container_name=/system.slice/var-lib-kubelet-pods-da352869\x2d8cc4\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d9bypt.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=630729i 1447789054000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/var-lib-kubelet-pods-da352869\x2d8cc4\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d9bypt.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789054000000000
# cpu/limit_gauge,container_name=/system.slice/var-lib-kubelet-pods-da352869\x2d8cc4\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d9bypt.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789054000000000
# memory/usage_bytes_gauge,container_name=/system.slice/var-lib-kubelet-pods-da352869\x2d8cc4\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d9bypt.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789054000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/var-lib-kubelet-pods-da352869\x2d8cc4\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d9bypt.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789054000000000
# memory/limit_bytes_gauge,container_name=/system.slice/var-lib-kubelet-pods-da352869\x2d8cc4\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d9bypt.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789054000000000
# memory/page_faults_cumulative,container_name=/system.slice/var-lib-kubelet-pods-da352869\x2d8cc4\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d9bypt.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789054000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/var-lib-kubelet-pods-da352869\x2d8cc4\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d9bypt.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789054000000000
# uptime_ms_cumulative,container_name=/system.slice/var-lib-kubelet-pods-da352869\x2d8cc4\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d9bypt.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=630729i 1447789057000000000
# cpu/usage_ns_cumulative,container_name=/system.slice/var-lib-kubelet-pods-da352869\x2d8cc4\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d9bypt.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789057000000000
# cpu/limit_gauge,container_name=/system.slice/var-lib-kubelet-pods-da352869\x2d8cc4\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d9bypt.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789057000000000
# memory/usage_bytes_gauge,container_name=/system.slice/var-lib-kubelet-pods-da352869\x2d8cc4\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d9bypt.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789057000000000
# memory/working_set_bytes_gauge,container_name=/system.slice/var-lib-kubelet-pods-da352869\x2d8cc4\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d9bypt.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789057000000000
# memory/limit_bytes_gauge,container_name=/system.slice/var-lib-kubelet-pods-da352869\x2d8cc4\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d9bypt.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789057000000000
# memory/page_faults_cumulative,container_name=/system.slice/var-lib-kubelet-pods-da352869\x2d8cc4\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d9bypt.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789057000000000
# memory/major_page_faults_cumulative,container_name=/system.slice/var-lib-kubelet-pods-da352869\x2d8cc4\x2d11e5\x2d8fb0\x2d08002750857f-volumes-kubernetes.io\x7esecret-default\x2dtoken\x2d9bypt.mount,host_id=10.245.1.3,hostname=10.245.1.3 value=0i 1447789057000000000
# uptime_ms_cumulative,container_name=/user.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=630718i 1447789054000000000
# cpu/usage_ns_cumulative,container_name=/user.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=449975568i 1447789054000000000
# cpu/limit_gauge,container_name=/user.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789054000000000
# memory/usage_bytes_gauge,container_name=/user.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=8192i 1447789054000000000
# memory/working_set_bytes_gauge,container_name=/user.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=4096i 1447789054000000000
# memory/limit_bytes_gauge,container_name=/user.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789054000000000
# memory/page_faults_cumulative,container_name=/user.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=20117i 1447789054000000000
# memory/major_page_faults_cumulative,container_name=/user.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=92i 1447789054000000000
# uptime_ms_cumulative,container_name=/user.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=630718i 1447789055000000000
# cpu/usage_ns_cumulative,container_name=/user.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=449975568i 1447789055000000000
# cpu/limit_gauge,container_name=/user.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=1000i 1447789055000000000
# memory/usage_bytes_gauge,container_name=/user.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=8192i 1447789055000000000
# memory/working_set_bytes_gauge,container_name=/user.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=4096i 1447789055000000000
# memory/limit_bytes_gauge,container_name=/user.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=-1i 1447789055000000000
# memory/page_faults_cumulative,container_name=/user.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=20117i 1447789055000000000
# memory/major_page_faults_cumulative,container_name=/user.slice,host_id=10.245.1.3,hostname=10.245.1.3 value=92i 1447789055000000000'''
#     ret = parse_influx(content)
#     for x in ret:
#         import json
#         print json.dumps(x)
    
#     # print parse_influx_event('uptime_ms_cumulative,container_base_image=kubernetes/heapster:canary,container_name=heapster,host_id=10.245.1.3,hostname=10.245.1.3,labels=k8s-app:heapster\,version:v6\,io.kubernetes.pod.name:kube-system/heapster-0agd6,namespace_id=4df2cba0-8cc0-11e5-8fb0-08002750857f,pod_id=316b9974-8cc4-11e5-8fb0-08002750857f,pod_name=heapster-0agd6,pod_namespace=kube-system value=68060984i 1447789050000000000')