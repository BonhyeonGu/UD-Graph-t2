#!/bin/bash

echo -e "host \t all \t all \t 0.0.0.0/0 \t md5" >> /etc/postgresql/9.4/main/pg_hba.conf
echo "listen_addresses='*'" >> /etc/postgresql/9.4/main/postgresql.conf
cat /etc/postgresql/9.4/main/pg_hba.conf | grep host
cat /etc/postgresql/9.4/main/postgresql.conf | grep listen_addresses
