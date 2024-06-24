This is what I wrote to deal with failover between my fiber ($) and cell ($$) connections.

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
