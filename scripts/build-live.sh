#!/usr/bin/env bash
# =============================================================================
#  build-live.sh : fabrique le mini-système LINUX servi aux PC par le réseau
#                  (celui qui restaure les images Clonezilla ; voir CDC).
#
#  Usage (sur la VM Debian, en root) :
#      build-live.sh [DATA_DIR]
#
#    DATA_DIR (facultatif) : défaut $DATA_DIR de l'environnement, sinon /srv/deploy.
#  Variables facultatives :
#      LB_MIRROR   miroir Debian (défaut http://deb.debian.org/debian/)
#      LB_DIST     version de Debian du mini-système (défaut bookworm)
#      KEEP_WORK=1 garde le dossier de travail (pour lire le journal de construction)
#
#  Ce que le script fait :
#    a. recopie la configuration live-build du dépôt (live/) dans un dossier de travail temporaire ;
#    b. y glisse le client de déploiement (client/, sans ses tests) dans /opt/mdtprox/client ;
#    c. lance « lb config » puis « lb build » (télécharge environ 400 Mo de paquets Debian, 10 à 30 min) ;
#    d. dépose vmlinuz, initrd.img et filesystem.squashfs dans DATA_DIR/live/ (remplacement en un coup).
#
#  L'adresse du serveur N'EST PAS écrite dans l'image : elle est passée au noyau par boot.ipxe
#  (paramètre mdtprox_server=). Le même système sert donc à la paillasse et à la production.
#  En revanche le client est recopié dans l'image : après toute modification de client/, relancer ce script.
#
#  RELANÇABLE. Rien n'est modifié dans DATA_DIR/live/ tant que la construction n'a pas réussi.
#  À VÉRIFIER SUR BANC : tout ce script (jamais exécuté : il faut une VM Debian 12 avec accès Internet).
# =============================================================================
set -euo pipefail

MIN_FREE_MB=8192                      # place libre exigée dans le dossier de travail
WORK_PARENT="/var/tmp"                # (pas /tmp : peut être en mémoire vive)
WORK_PREFIX="mdtprox-live"             # dossier de travail = /var/tmp/mdtprox-live.XXXXXX
LOG_COPY="/var/log/mdtprox-live-build.log"

info() { printf '\n==> %s\n' "$*"; }
warn() { printf '\n/!\\ ATTENTION : %s\n' "$*" >&2; }
die()  { printf '\nERREUR : %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Ce script doit être lancé en root (live-build monte des systèmes de fichiers)."

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
DATA_DIR="${1:-${DATA_DIR:-/srv/deploy}}"
[[ "$DATA_DIR" == /* ]] || die "DATA_DIR doit être un chemin absolu (reçu : « $DATA_DIR »)."

for cmd in lb debootstrap mksquashfs sha256sum find tar cp install df mktemp awk; do
    command -v "$cmd" >/dev/null 2>&1 \
        || die "Commande introuvable : $cmd (sur Debian : apt-get install live-build debootstrap squashfs-tools ; ou lancez scripts/setup-vm.sh)."
done
[[ -x "$REPO/live/auto/config" && -d "$REPO/live/config" ]] || die "Configuration live-build introuvable dans $REPO/live (dépôt incomplet ?)."
[[ -f "$REPO/client/deploy_client.py" && -f "$REPO/client/linux_restore.py" ]] || die "Le client est introuvable dans $REPO/client."

free_mb="$(df -Pm "$WORK_PARENT" | awk 'NR==2 {print $4}')"
[[ "${free_mb:-0}" -ge "$MIN_FREE_MB" ]] || die "Il faut au moins $MIN_FREE_MB Mo libres dans $WORK_PARENT (disponible : ${free_mb:-?} Mo)."

WORK="$(mktemp -d "$WORK_PARENT/$WORK_PREFIX.XXXXXX")"

# SÉCURITÉ : live-build monte /proc, /sys et /dev dans son dossier de travail. Supprimer ce dossier alors
# qu'un montage subsiste effacerait des fichiers du SYSTÈME. On vérifie donc d'abord qu'il n'en reste aucun.
cleanup() {
    local rc=$?
    case "$WORK" in "$WORK_PARENT/$WORK_PREFIX".*) ;; *) return "$rc" ;; esac
    if awk '{print $2}' /proc/mounts | grep -qF -- "$WORK"; then
        warn "Des montages subsistent sous $WORK : le dossier n'est PAS supprimé (« umount -R » sur ces montages, ou redémarrez la VM)."
        return "$rc"
    fi
    if [[ "${KEEP_WORK:-0}" == "1" ]]; then
        echo "Dossier de travail gardé : $WORK"
        return "$rc"
    fi
    rm -rf -- "$WORK"
    return "$rc"
}
trap cleanup EXIT

# ------------------------------------------------ a/b. configuration + client ---
info "1/4 Préparation de la configuration (dossier de travail : $WORK)"
cp -a "$REPO/live/." "$WORK/"
CLIENT_DEST="$WORK/config/includes.chroot/opt/mdtprox/client"
install -d "$(dirname "$CLIENT_DEST")"
cp -a "$REPO/client" "$CLIENT_DEST"
rm -rf -- "$CLIENT_DEST/tests" "$CLIENT_DEST/.pytest_cache" "$CLIENT_DEST/client.json"
find "$CLIENT_DEST" -type d -name __pycache__ -prune -exec rm -rf -- {} +
[[ -f "$CLIENT_DEST/deploy_client.py" ]] || die "Le client n'a pas été copié correctement."
chmod 0755 "$WORK/config/includes.chroot/opt/mdtprox/start.sh" "$WORK/auto/config" "$WORK"/config/hooks/normal/*.hook.chroot

# ---------------------------------------------------------- c. construction ---
info "2/4 Construction (lb config puis lb build : plusieurs minutes, ne pas interrompre)"
export LB_MIRROR="${LB_MIRROR:-http://deb.debian.org/debian/}" LB_DIST="${LB_DIST:-bookworm}"
echo "Debian $LB_DIST, miroir $LB_MIRROR"
build() {
    cd "$WORK"
    lb config && lb build
}
# Le journal complet est gardé ; à l'écran, seulement les étapes de live-build et les alertes.
if ! build 2>&1 | tee "$WORK/build.log" | { grep --line-buffered -E '^(\[|E: |W: )' || true; }; then
    cp -f -- "$WORK/build.log" "$LOG_COPY" 2>/dev/null || true
    tail -n 30 "$WORK/build.log" >&2 || true
    die "La construction a échoué. Journal complet : $LOG_COPY (relancez avec KEEP_WORK=1 pour garder le dossier)."
fi
cp -f -- "$WORK/build.log" "$LOG_COPY" 2>/dev/null || true

# ------------------------------------------------- d. récupérer les 3 fichiers ---
info "3/4 Recherche du noyau, de l'initrd et du système de fichiers"
find_one() {  # find_one DOSSIER MOTIF : le dernier fichier trouvé, hors du chroot
    find "$1" -path "$WORK/chroot" -prune -o -type f -name "$2" -print 2>/dev/null | sort | tail -n 1
}
locate_all() {
    kernel="$(find_one "$1" 'vmlinuz*')"
    initrd="$(find_one "$1" 'initrd.img*')"
    squash="$(find_one "$1" 'filesystem.squashfs')"
}
kernel="" initrd="" squash=""
locate_all "$WORK/binary"
if [[ -z "$kernel" || -z "$initrd" || -z "$squash" ]]; then
    # Selon la version de live-build, le résultat « netboot » est une archive : on l'ouvre.
    archive="$(find "$WORK" -maxdepth 1 -type f \( -name '*.tar' -o -name '*.tar.*' -o -name '*.tgz' \) -print | sort | head -n 1)"
    if [[ -n "$archive" ]]; then
        install -d "$WORK/extract"
        tar -xf "$archive" -C "$WORK/extract"
        locate_all "$WORK/extract"
    fi
fi
[[ -n "$kernel" && -n "$initrd" && -n "$squash" ]] \
    || die "Fichiers introuvables après la construction (noyau : '${kernel:-?}', initrd : '${initrd:-?}', squashfs : '${squash:-?}'). Voir $LOG_COPY."

size_of() { stat -c %s -- "$1"; }
[[ "$(size_of "$kernel")" -gt 1000000 ]]    || die "Le noyau trouvé est anormalement petit : $kernel"
[[ "$(size_of "$initrd")" -gt 5000000 ]]    || die "L'initrd trouvé est anormalement petit : $initrd"
[[ "$(size_of "$squash")" -gt 50000000 ]]   || die "Le système de fichiers trouvé est anormalement petit : $squash"

# ------------------------------------------------------ e. mise en place ---
info "4/4 Mise en place dans $DATA_DIR/live (remplacement en un coup)"
install -d -m 0755 "$DATA_DIR"
NEW="$(mktemp -d "$DATA_DIR/.live.new.XXXXXX")"
install -m 0644 -- "$kernel" "$NEW/vmlinuz"
install -m 0644 -- "$initrd" "$NEW/initrd.img"
install -m 0644 -- "$squash" "$NEW/filesystem.squashfs"
chmod 0755 "$NEW"
if [[ -e "$DATA_DIR/live" ]]; then
    OLD="$DATA_DIR/.live.old.$$"
    mv -- "$DATA_DIR/live" "$OLD"
    mv -- "$NEW" "$DATA_DIR/live"
    rm -rf -- "$OLD"
else
    mv -- "$NEW" "$DATA_DIR/live"
fi

echo
echo "============================================================================"
echo " Mini-système Linux prêt dans $DATA_DIR/live/ :"
for f in vmlinuz initrd.img filesystem.squashfs; do
    printf '   %-22s %6d Mo   SHA-256 %s\n' "$f" "$(( $(size_of "$DATA_DIR/live/$f") / 1048576 ))" \
        "$(sha256sum -- "$DATA_DIR/live/$f" | cut -c1-16)…"
done
echo
echo " Les PC le téléchargent via boot.ipxe (MINI_OS=linux dans scripts/setup-vm.sh)."
echo " Vérification depuis un autre poste :  curl -I http://<IP-de-cette-VM>:8080/live/filesystem.squashfs"
echo " RAM conseillée sur les PC : 2 Go minimum (le système se charge en mémoire)."
echo "============================================================================"
