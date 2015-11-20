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