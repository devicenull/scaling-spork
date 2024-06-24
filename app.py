#!/usr/bin/python3

import argparse
import ipaddress
import logging as l
import json
import re
import subprocess
import sys

from interfaceparsers import parse_dhclient, get_route_table, parse_mmcli

# table IDs from /etc/iproute2/rt_tables
CABLE_INTERFACE="enp1s0"
CABLE_TABLE="30"
CELL_INTERFACE="wwan0"
CELL_TABLE="40"

"""
Things to disable on cell:
    * sabnzbd
    * plex

TODO:
    * monitor cell interface, reconnect as necessary
"""

l.basicConfig(stream=sys.stdout, level=l.DEBUG)

# checks should return True if an interface reload is required

def check_interface_ip(interface):
    # [{"ifindex":2,"ifname":"eno1","flags":["BROADCAST","MULTICAST","UP","LOWER_UP"],"mtu":1500,"qdisc":"fq","operstate":"UP","group":"default","txqlen":1000,"altnames":["enp0s31f6"],"addr_info":[{"family":"inet","local":"192.168.5.134","prefixlen":24,"broadcast":"192.168.5.255","scope":"global","label":"eno1","valid_life_time":4294967295,"preferred_life_time":4294967295}]}]
    ipdata = json.loads(subprocess.check_output('ip -j -4 addr show dev %s' % interface , shell=True))
    if len(ipdata) == 0:
        l.info('no ip address found on %s' % interface)
        return False 
    return False


def check_gateway_pings(table, interface):
    # [{"dst":"default","gateway":"192.168.5.1","dev":"eno1","flags":["onlink"]}]
    ipdata = json.loads(subprocess.check_output('ip -j -4 route show default table %s' % table, shell=True))
    try:
        gateway = ipdata[0]['gateway']
    except IndexError:
        l.info('unable to determine gateway ip on table %s' % table)
        l.info(ipdata)
        return True
    try:
        subprocess.check_output('ping -c 2 %s -m %s -I %s 2>&1' % (gateway, table, interface), shell=True)
    except subprocess.CalledProcessError as e:
        l.info('gateway %s fails to ping via table %s' % (gateway, table))
        l.info(e.output.decode('ascii').strip())
        return True

    return False


def check_external_ips(table,interface):
    googleping = False
    try:
        subprocess.check_output('ping -c 2 8.8.8.8 -m %s -I %s 2>&1' % (table, interface), shell=True)
        googleping = True
    except subprocess.CalledProcessError as e:
        l.info('8888 fails to ping via table %s' % table)
        l.info(e.output.decode('ascii').strip())

    if not googleping:
        wpcomping = False
        try:
            subprocess.check_output('ping -c 2 192.0.78.9 -m %s -I %s 2>&1' % (table, interface), shell=True)
            wpcomping = True
        except subprocess.CalledProcessError as e:
            l.info('wpcom failed to ping via table %s' % table)
            l.info(e.output.decode('ascii').strip())

    # only reload if both fail to ping
    return not googleping and not wpcomping


def get_primary_interface():
    ipdata = json.loads(subprocess.check_output('ip -j -4 route show default', shell=True))
    try:
        return ipdata[0]['dev']
    except IndexError:
        return ''


# If the table doesn't exist, or the IPs have changed, blow it away & start again
def create_route_table(table_id, interface, source_ip, default_gateway, subnet):
    l.info('generating route table for %s' % table_id)
    subprocess.call('ip route flush table %s' % table_id, shell=True)
    subprocess.call('ip route add %s scope link dev %s src %s table %s' % (subnet, interface, source_ip, table_id), shell=True)
    subprocess.call('ip route add default via %s dev %s src %s table %s' % (default_gateway, interface, source_ip, table_id), shell=True)


def set_default_route(interface, default_gateway):
    l.info('setting default gateway to %s via %s' % (default_gateway, interface))
    subprocess.call('ip route del default', shell=True)
    subprocess.call('ip route add default via %s dev %s' % (default_gateway, interface), shell=True)

def get_valid_lease(interface):
    for cur in parse_dhclient('/var/lib/dhcp/dhclient.leases'):
        if cur['interface'] == CABLE_INTERFACE and not cur['expired']:
            l.info('found lease for %s, addr %s, expires %s' % (CABLE_INTERFACE, cur['fixed-address'], cur['expires'])) 
            return cur

    return []


parser = argparse.ArgumentParser()
parser.add_argument('--configure_cell', help='Configure cell modem IP addresses based on ModemManager', action='store_true')
parser.add_argument('--cron', help='Running from cron, do connectivity checks', action='store_true')
parser.add_argument('--failover', help='Manually fail over to an interface (expected: cell or cable)')
args = parser.parse_args()

if args.configure_cell:
    cell_lease = parse_mmcli()
    if len(cell_lease) > 0:
        subprocess.call('ip -4 addr flush dev %s' % CELL_INTERFACE, shell=True)
        subprocess.call('ip -4 addr add %s/%s dev %s' % (cell_lease['fixed-address'], cell_lease['subnet-mask'], CELL_INTERFACE), shell=True)
        # we don't configure a default route, that's left up to the cron (this action is called on boot)

        net = ipaddress.ip_network('%s/%s' % (cell_lease['fixed-address'], cell_lease['subnet-mask']), strict=False)
        create_route_table(CELL_TABLE, CELL_INTERFACE, cell_lease['fixed-address'], cell_lease['routers'], str(net))
elif args.cron:
    cable_lease = get_valid_lease(CABLE_INTERFACE)
    cable_rt = get_route_table(CABLE_TABLE)
    for cur in parse_dhclient('/var/lib/dhcp/dhclient.leases'):
        if cur['interface'] == CABLE_INTERFACE and not cur['expired']:
            l.info('found lease for %s, addr %s, expires %s' % (CABLE_INTERFACE, cur['fixed-address'], cur['expires'])) 
            cable_lease = cur

    cable_rt = get_route_table(CABLE_TABLE)
    if (len(cable_rt) == 0 and len(cable_lease) > 0) or (cable_lease['fixed-address'] != cable_rt['source_ip']):
        net = ipaddress.ip_network('%s/%s' % (cable_lease['fixed-address'], cable_lease['subnet-mask']), strict=False)
        create_route_table(CABLE_TABLE, CABLE_INTERFACE, cable_lease['fixed-address'], cable_lease['routers'], str(net))

    cell_lease = parse_mmcli()
    cell_rt = get_route_table(CELL_TABLE)
    if (len(cell_rt) == 0 and len(cell_lease) > 0) or (cell_lease['fixed-address'] != cell_rt['source_ip']):
        net = ipaddress.ip_network('%s/%s' % (cell_lease['fixed-address'], cell_lease['subnet-mask']), strict=False)
        create_route_table(CELL_TABLE, CELL_INTERFACE, cell_lease['fixed-address'], cell_lease['routers'], str(net))


    want_interface_reload = check_interface_ip(CABLE_INTERFACE) or check_gateway_pings(CABLE_TABLE, CABLE_INTERFACE) or check_external_ips(CABLE_TABLE, CABLE_INTERFACE)
    if (get_primary_interface() == CABLE_INTERFACE or get_primary_interface() == '') and want_interface_reload:
        l.info('Reloading %s' % CABLE_INTERFACE)
        subprocess.call('ifdown enp1s0; ifup enp1s0', shell=True)

        cable_lease = get_valid_lease(CABLE_INTERFACE)
        net = ipaddress.ip_network('%s/%s' % (cable_lease['fixed-address'], cable_lease['subnet-mask']), strict=False)
        create_route_table(CABLE_TABLE, CABLE_INTERFACE, cable_lease['fixed-address'], cable_lease['routers'], str(net))

        is_interface_fixed = not (check_interface_ip(CABLE_INTERFACE) or check_gateway_pings(CABLE_TABLE, CABLE_INTERFACE) or check_external_ips(CABLE_TABLE, CABLE_INTERFACE))
        if is_interface_fixed:
            l.info('Reload completed, cable now works')
        else:
            l.info('Cable connection failed, failing over')
            set_default_route(CELL_INTERFACE, cell_lease['routers'])
    elif get_primary_interface() == CABLE_INTERFACE and not want_interface_reload:
        # in this case, let's send some traffic over the cell interface so it doesn't time out on us
        subprocess.check_output('ping -c 1 8.8.8.8 -m %s 2>&1' % CELL_TABLE, shell=True)
    elif get_primary_interface() == CELL_INTERFACE and not want_interface_reload:
        l.info('Cable connection has come back, failing over')
        set_default_route(CABLE_INTERFACE, cable_lease['routers'])
    elif get_primary_interface() == CELL_INTERFACE and want_interface_reload:
        l.info('Lets try reloading cable again and hope for the best')
        subprocess.call('ifdown enp1s0; ifup enp1s0', shell=True)

elif args.failover:
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
