This is what I wrote to deal with failover between my fiber and cell connections.

The general rule is be connected to fiber whenever possible (referred to as "cable", because my
old cable ISP is providing fiber now, and the association is hard to break).  Failover to cell happens
automatically whenever the DHCP lease for the cable modem expires (or the script notices connectivity
via that interface doesn't work)

This requires setting up udchp like so:

```
auto vlan23
iface vlan23 inet static
        bridge-ports enp2s0.23 enp3s0.23
        ip-forward 1
        post-up /usr/sbin/udhcpc -i vlan23 -b -A 5 -s /etc/udhcpc/default.script -p /var/run/uhcpc.vlan23.pid
        post-up /usr/sbin/tc qdisc replace dev enp1s0 root fq_codel
        post-down /usr/bin/kill `cat /var/run/uhcpc.vlan23.pid`

```

and then requires modifying `/etc/udhcpc/default.script` to set a different METRIC for each interface.  Lowest
metric wins, so this would generally be the fiber one (in my case that ones get metric 50, and cell gets metric 100)

Don't forget the general iptables NAT setup for both sides

The general theory here is both connections are active by default and present in the default routing table
The script does probes of the fiber connection to ensure it's health (checking 8.8.8.8 and wordpress.com,
in the hopes that both aren't down at once... and if the latter is down I'll already know).  If any
of the health checks fail, we swap the default gateway over to the cell modem.

I had tried using multiple routing tables for this in the past, however it gets tricky.  Most of the standard
linux tools like udhcpc don't really deal with them that well, so it requires custom hacks everywhere.  It ended
up not being nearly as reliable as I'd want

