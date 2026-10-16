#!/bin/sh
# Reload nginx after any certificate renewal (webroot certs otherwise keep serving the old cert).
nginx -t -q && systemctl reload nginx
