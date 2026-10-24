#!/bin/sh
# MDTPROX - lancé sur la console 1 par mdtprox-client.service (voir CDC).
# Paramètres du noyau, écrits par boot-linux.ipxe :
#   mdtprox_server=http://IP:8080   adresse de l'API (le même système sert pour la paillasse et pour la production)
#   mdtprox_debug                   ouvre un shell root à la place du client (dépannage : à ajouter dans l'invite iPXE)
# Les variables MDTPROX_* ne servent qu'aux tests.
CMDLINE="${MDTPROX_CMDLINE:-/proc/cmdline}"
SHELL_CMD="${MDTPROX_SHELL:-/bin/bash}"
PYTHON="${MDTPROX_PYTHON:-python3}"
CLIENT_DIR="${MDTPROX_CLIENT_DIR:-/opt/mdtprox/client}"

param() { tr ' ' '\n' < "$CMDLINE" | sed -n "s/^$1=//p" | head -n 1; }
has()   { tr ' ' '\n' < "$CMDLINE" | grep -Eq "^$1(=|\$)"; }

setterm --blank 0 --powerdown 0 2>/dev/null || true   # l'écran ne doit pas s'éteindre pendant une installation
export LANG=C.UTF-8 TERM=linux

if has mdtprox_debug; then
    echo "Mode dépannage (mdtprox_debug) : shell root, le client n'est pas lancé."
    echo "Pour le lancer à la main : cd $CLIENT_DIR && python3 deploy_client.py --server http://IP:8080"
    exec "$SHELL_CMD" -l
fi

server="$(param mdtprox_server)"
cd "$CLIENT_DIR" || exit 1
if [ -n "$server" ]; then
    "$PYTHON" deploy_client.py --server "$server"
else
    "$PYTHON" deploy_client.py
fi
code=$?
[ "$code" -eq 0 ] && exit 0   # installé (le client a lui-même redémarré le PC) ou pénalité (extinction)

# Échec : on laisse la main pour dépanner, sans redémarrer en boucle.
echo
echo "============================================================================"
echo " Le client s'est arrêté avec une erreur (code $code). Un shell de dépannage est ouvert."
echo "   ip -br a                        le PC a-t-il une adresse IP ?"
echo "   lsblk                           les disques et partitions"
echo "   python3 deploy_client.py        relancer le client (dans $CLIENT_DIR)"
echo "   reboot  /  poweroff             redémarrer / éteindre"
echo "============================================================================"
exec "$SHELL_CMD" -l
