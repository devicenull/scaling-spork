This is what I wrote to deal with failover between my fiber and cell connections.

The general rule is be connected to fiber whenever possible (referred to as "cable", because my
old cable ISP is providing fiber now, and the association is hard to break).  Failover to cell
whenever the internet has been down for a bit.

This also handles configuring the cell modem, because I refuse to run networkmanager anywhere, so 
it talks to modemmanager to get IPs and runs the relevant IP commands.

This uses routing table 30 for the fiber connection, and 40 for cell.  These need to be pre-created
in /etc/iproute2/rt_tables by adding the following:

```
30 cable
40 cell
```

It also depends on some ip rules being added every boot

```
ip rule add fwmark 30 table 30
ip rule add fwmark 40 table 40
```

There's also some /e/n/i cruft:

```
auto wwan0
iface wwan0 inet static
        pre-up mmcli --modem=0 --enable
        pre-up mmcli --modem=0 --simple-connect='apn=FIXME,ip-type=ipv4v6'
        up /root/monitor_internet/app.py --configure_cell
        post-down mmcli --modem=0 --disable
```

and also the general iptables NAT setup for both sides

The general theory here is both connections are active by default with their own routing table. 
The script does probes of the fiber connection to ensure it's health (checking 8.8.8.8 and wordpress.com,
in the hopes that both aren't down at once... and if the latter is down I'll already know).  If any
of the health checks fail, we swap the default gateway over to the cell modem.  As soon as fiber returns,
it gets swapped back.

There's some unanswered things here, like does my IP via the cell network ever expire?  I haven't had sessions
up long enough to know if that's a problem or not.  Similarly, IPv6 is a whole other can of worms.  My ISP
doesn't currently provide IPv6, so I haven't really addressed it (it doesn't make sense to have it only when
failed over to the cell network).
