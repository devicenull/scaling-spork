#!/usr/bin/python3

import argparse
import ipaddress
import logging as l
import json
import re
import subprocess
import sys

import MySQLdb
import requests

from configparser import ConfigParser
from interfaceparsers import parse_dhclient, get_route_table, get_peplink_info, parse_udhcpc
from logging.handlers import RotatingFileHandler

# table IDs from /etc/iproute2/rt_tables
CABLE_INTERFACE="enp1s0"
CELL_INTERFACE="vlan23"

"""
Things to disable on cell:
    * sabnzbd
    * plex

TODO:
    * monitor cell interface, reconnect as necessary
"""

l.basicConfig(handlers=[
        RotatingFileHandler('/var/log/monitor_internet.log', maxBytes=1024*1024*1024*10, backupCount=2),
        l.StreamHandler(sys.stdout),
    ],
    level=l.DEBUG,
    format="[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s",
    datefmt='%Y-%m-%d %H:%M:%S')

def exception_handler(extype, exval, extrace):
    l.error("unhandled exception", exc_info=(extype,exval,extrace))

sys.excepthook = exception_handler

# checks should return True if an interface reload is required

def check_interface_ip(interface):
    # [{"ifindex":2,"ifname":"eno1","flags":["BROADCAST","MULTICAST","UP","LOWER_UP"],"mtu":1500,"qdisc":"fq","operstate":"UP","group":"default","txqlen":1000,"altnames":["enp0s31f6"],"addr_info":[{"family":"inet","local":"192.168.5.134","prefixlen":24,"broadcast":"192.168.5.255","scope":"global","label":"eno1","valid_life_time":4294967295,"preferred_life_time":4294967295}]}]
    ipdata = json.loads(subprocess.check_output('ip -j -4 addr show dev %s' % interface , shell=True))
    if len(ipdata) == 0:
        l.info('no ip address found on %s' % interface)
        return False 
    return False


def check_gateway_pings(interface):
    # [{"dst":"default","gateway":"192.168.5.1","dev":"eno1","flags":["onlink"]}]
    ipdata_raw = subprocess.check_output('ip -j -4 route show default oif %s 2>&1' % interface, shell=True)
    ipdata = json.loads(ipdata_raw)
    try:
        gateway = ipdata[0]['gateway']
    except IndexError:
        l.info('unable to determine gateway ip on interface %s' % interface)
        debug_raw = subprocess.check_output('ip -4 route show oif %s' % interface, shell=True)
        l.info(debug_raw)
        return True
    try:
        subprocess.check_output('ping -c 2 %s -I %s 2>&1' % (gateway, interface), shell=True)
    except subprocess.CalledProcessError as e:
        l.info('gateway %s fails to ping via interface %s' % (gateway, interface))
        l.info(e.output.decode('ascii').strip())
        return True

    return False


def check_external_ips(interface):
    googleping = False
    try:
        subprocess.check_output('ping -c 2 8.8.8.8 -I %s 2>&1' % (interface), shell=True)
        googleping = True
    except subprocess.CalledProcessError as e:
        l.info('8888 fails to ping via interface %s' % interface)
        l.info(e.output.decode('ascii').strip())

    if not googleping:
        wpcomping = False
        try:
            subprocess.check_output('ping -c 2 192.0.78.9 -I %s 2>&1' % (interface), shell=True)
            wpcomping = True
        except subprocess.CalledProcessError as e:
            l.info('wpcom failed to ping via interface %s' % interface)
            l.info(e.output.decode('ascii').strip())

    # only reload if both fail to ping
    return not googleping and not wpcomping


def get_primary_interface():
    ipdata = json.loads(subprocess.check_output('ip -j -4 route get 8.8.8.8', shell=True))
    try:
        return ipdata[0]['dev']
    except IndexError:
        return ''



def set_default_route(interface, default_gateway):
    pass
    # FIXME
    l.info('setting default gateway to %s via %s' % (default_gateway, interface))
    # /e/n/i hates setting this for some reason
    subprocess.call('sysctl net.ipv4.conf.%s.forwarding=1' % interface, shell=True)
    subprocess.call('ip route del default', shell=True)
    subprocess.call('ip route add default via %s dev %s' % (default_gateway, interface), shell=True)

def sendsms(config, message):
    r = requests.post('https://piscatawaynjmeetings.com/sendsms.php', data={'token':config.get('main', 'smskey'), 'message':message})



default_config = """
[main]
smskey=
peplinkuser=
peplinkpass=

[database]
host=
user=
password=
database=
"""

config = ConfigParser()
config.read_string(default_config)
config.read('/root/monitor_internet/config.ini')

parser = argparse.ArgumentParser()
parser.add_argument('--cron', help='Running from cron, do connectivity checks', action='store_true')
parser.add_argument('--failover', help='Manually fail over to an interface (expected: cell or cable)')
args = parser.parse_args()

if args.cron:
    #cable_lease = get_valid_lease(CABLE_INTERFACE)
    cable_lease = parse_udhcpc(CABLE_INTERFACE)
    cell_lease = parse_udhcpc(CELL_INTERFACE)
    l.debug("cable lease %s" % cable_lease)
    l.debug("cell lease %s" % cell_lease)
    want_interface_reload = check_interface_ip(CABLE_INTERFACE) or check_gateway_pings(CABLE_INTERFACE) or check_external_ips(CABLE_INTERFACE)
    if (get_primary_interface() == CABLE_INTERFACE or get_primary_interface() == '') and want_interface_reload:
        l.info('Reloading %s' % CABLE_INTERFACE)
        subprocess.call('/usr/sbin/ifdown enp1s0; /usr/sbin/ifup enp1s0', shell=True)

        cable_lease = get_valid_lease(CABLE_INTERFACE)
        net = ipaddress.ip_network('%s/%s' % (cable_lease['fixed-address'], cable_lease['subnet-mask']), strict=False)

        is_interface_fixed = not (check_interface_ip(CABLE_INTERFACE) or check_gateway_pings(CABLE_INTERFACE) or check_external_ips(CABLE_INTERFACE))
        if is_interface_fixed:
            l.info('Reload completed, cable now works')
            set_default_route(CABLE_INTERFACE, cable_lease['routers'])
            sendsms(config, 'Reloaded cable interface, fixed internet')
        else:
            l.info('Cable connection failed, failing over')
            set_default_route(CELL_INTERFACE, cell_lease['gateway'])
            sendsms(config, 'Cable interface down, failing over')
    elif get_primary_interface() == CABLE_INTERFACE and not want_interface_reload:
        # in this case, let's send some traffic over the cell interface so it doesn't time out on us
        # subprocess.check_output('ping -c 1 8.8.8.8 -m %s 2>&1' % CELL_TABLE, shell=True)
        # new cell modem takes care of pinging stuff for us
        pass
    elif get_primary_interface() == CELL_INTERFACE and not want_interface_reload:
        l.info('Cable connection has come back, failing over')
        set_default_route(CABLE_INTERFACE, cable_lease['routers'])
        sendsms(config, 'Cable connection has come back, failing back over')
    elif get_primary_interface() == CELL_INTERFACE and want_interface_reload:
        l.info('Lets try reloading cable again and hope for the best')
        subprocess.call('/usr/sbin/ifdown enp1s0; /usr/sbin/ifup enp1s0', shell=True)

    # record stats to mysql - do this last so we don't really have to care about timeouts
    cell_stats = get_peplink_info(config)
    if 'stats' in cell_stats:
        db = MySQLdb.connect(host=config.get('database', 'host'), user=config.get('database', 'user'), password=config.get('database', 'password'), database=config.get('database', 'database'))
        stats = cell_stats['stats'][0]
        cursor = db.cursor()
        cursor.execute("insert into cell_stats(date, channel, rssi, sinr, rsrp, rsrq) values(NOW(), %s, %s, %s, %s, %s)", (stats['channel'], stats['signal']['rssi'], stats['signal']['sinr'], stats['signal']['rsrp'], stats['signal']['rsrq']))
        db.commit()


elif args.failover:
    pass
    #FIXME
    if args.failover == 'cable':
        rt = get_route_table(CABLE_TABLE)
        interface = CABLE_INTERFACE
    elif args.failover == 'cell':
        rt = get_route_table(CELL_TABLE)
        interface = CELL_INTERFACE
    else:
        l.error('uhh what interface?')

    if len(rt) > 0:
        l.info('Updating default route to %s' % rt['default_gateway'])
        set_default_route(rt['default_interface'], rt['default_gateway'])
    else:
        l.error('That interface seems down')

else:
    parser.print_usage()
