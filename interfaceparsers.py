import ipaddress
import json
import re
import subprocess
import requests

from datetime import datetime

# {'interface': 'enp4s0', 'fixed-address': '192.168.5.209', 'subnet-mask': '255.255.255.0', 'dhcp-lease-time': '7200', 'routers': '192.168.5.1', 'dhcp-message-type': '5', 'dhcp-server-identifier': '192.168.5.1', 'domain-name-servers': '192.168.5.1', 'domain-name': '"localdomain"', 'expires': '2020/10/19 19:36:07'}
def parse_dhclient(filename):
    leases = []
    leasedata = {}
    with open(filename, 'r') as f:
        for line in f:
            line = line.strip()
            if line == 'lease {':
                if len(leasedata) > 0:
                    leases.append(leasedata)
                    leasedata = {}
            elif matches := re.match('interface "([0-9a-z]+)"', line):
                leasedata['interface'] = matches[1]
            elif matches := re.match('option ([^ ]+) ([^ ]+);', line):
                leasedata[matches[1]] = matches[2]
            elif matches := re.match('fixed-address ([0-9\.]+);', line):
                leasedata['fixed-address'] = matches[1]
            elif matches := re.match('expire [0-9]+ (.+);', line):
                leasedata['expires'] = matches[1]
                leasedata['expired'] = datetime.strptime(leasedata['expires'], '%Y/%m/%d %H:%M:%S') < datetime.now()

    if len(leasedata) > 0:
        leases.append(leasedata)
    return leases

def get_route_table(table_id):
    try:
        ipdata = json.loads(subprocess.check_output('ip -j -4 route show table %s 2>/dev/null' % table_id, shell=True))
    except subprocess.CalledProcessError:
        # ip route show table X crashes if the table doesn't exist
        return []

    if len(ipdata) == 0:
        return []

    ret = {}
    for route in ipdata:
        if route['dst'] == 'default':
            ret['default_gateway'] = route['gateway']
            ret['default_interface'] = route['dev']

    # no default route, assume the rest of the table is crap
    if 'default_gateway' not in ret:
        return []

    # run through it again, I don't know if route order is guarenteed
    for route in ipdata:
        if route['dst'] == 'default':
            continue

        net = ipaddress.ip_network(route['dst'])
        if ipaddress.ip_address(ret['default_gateway']) in net:
            ret['subnet'] = route['dst']
            ret['source_ip'] = route['prefsrc']

            return ret

    # no on-link route?  that's also bad
    return []

def parse_mmcli():
    try:
        ipdata = json.loads(subprocess.check_output('mmcli --modem=0 --bearer=1 -J', shell=True))
    except subprocess.CalledProcessError:
        return []

    # try to match parse_dhclient for simplicity
    return {
        'fixed-address': ipdata['bearer']['ipv4-config']['address'],
        'routers': ipdata['bearer']['ipv4-config']['gateway'],
        'subnet-mask': ipdata['bearer']['ipv4-config']['prefix'],
    }

# the peplink dhcp server is bad and should feel bad about it
def get_peplink_info(config):
    baseurl = 'http://192.168.51.1/cgi-bin/MANGA/api.cgi'
    session = requests.Session()
    r = session.post(baseurl, json = {'username': config.get('main', 'peplinkuser'), 'password':config.get('main', 'peplinkpass'), 'func': 'login'})

    r = session.get(baseurl + '?func=status.wan.connection')
    js = r.json()

    return {
        'ip': js['response']['2']['ip'],
        'gateway': js['response']['2']['gateway'],
        'mask': js['response']['2']['mask'],
        'dns': js['response']['2']['dns'],
        'stats': js['response']['2']['cellular']['rat'][0]['band'],
    }

