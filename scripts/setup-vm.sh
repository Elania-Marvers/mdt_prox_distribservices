#!/usr/bin/env bash
# =============================================================================
#  setup-vm.sh : installe TOUT le service sur la VM Debian 12
#                (API + serveur TFTP + fichiers de démarrage réseau).
#
#  À lancer UNE FOIS dans la VM, en root, depuis le dépôt cloné dans /opt :
#      sudo -i
#      apt-get install -y git
#      git clone <URL-du-dépôt> /opt/mdtprox
#      /opt/mdtprox/scripts/setup-vm.sh
#
#  RELANÇABLE sans casse : chaque étape vérifie l'état avant d'agir. Ce que
#  l'on a déjà personnalisé n'est JAMAIS écrasé : /etc/mdtprox.env et
#  /srv/deploy/profiles.yaml ne sont créés que s'ils n'existent pas.
#
#  Ce que le script fait (dans l'ordre) :
#    1. prépare /srv/tftp et la configuration TFTP de dnsmasq ;
#    2. installe les paquets Debian (python3-venv, dnsmasq, ipxe, wimtools, 7z, curl) ;
#    3. crée l'utilisateur système « mdt » ;
#    4. crée l'environnement Python (venv) et installe l'API ;
#    5. crée /srv/deploy (A.2 du CDC) et copie ipxe.efi / undionly.kpxe dans /srv/tftp ;
#    6. télécharge wimboot (version épinglée, empreinte vérifiée) ;
#    7. installe boot.ipxe, /etc/mdtprox.env, profiles.yaml (si absents) ;
#    8. installe les services systemd, ouvre le pare-feu s'il y en a un, démarre.
#
#  Mini-système servi aux PC (variable MINI_OS, défaut « linux ») :
#      linux : mini-système Linux qui restaure les images CLONEZILLA de l'établissement (voir CDC) ;
#              à fabriquer ensuite avec scripts/build-live.sh ;
#      winpe : mini-Windows qui applique des images .wim (voir CDC) ;
#              à fabriquer ensuite avec scripts/build-winpe.sh.
#      Exemple :  MINI_OS=winpe /opt/mdtprox/scripts/setup-vm.sh
#  Un seul des deux est actif à la fois (c'est /srv/deploy/boot.ipxe qui choisit) ; relancer
#  ce script avec l'autre valeur de MINI_OS suffit pour basculer (l'ancien boot.ipxe est gardé en .bak).
#
#  CE SCRIPT NE TOUCHE PAS au DHCP ni au réseau de l'établissement.
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------- Réglages ---
INSTALL_DIR="/opt/mdtprox"        # le dépôt Git + l'environnement Python (.venv)
DATA_DIR="/srv/deploy"               # DATA_DIR du serveur (volumineux ou secret : jamais dans Git)
TFTP_DIR="/srv/tftp"                 # ipxe.efi, undionly.kpxe
ENV_FILE="/etc/mdtprox.env"       # secrets, droits 0600
SVC_USER="mdt"                       # utilisateur système qui fait tourner l'API
MINI_OS="${MINI_OS:-linux}"          # mini-système servi aux PC : linux (Clonezilla) ou winpe (.wim)

# wimboot : chargeur qui démarre WinPE depuis HTTP (https://ipxe.org/wimboot).
# Version et empreinte épinglées. Empreinte vérifiée le 2026-10-23 : calculée sur le
# fichier téléchargé ET identique au « digest » publié par GitHub pour cette version.
# Pour changer de version : modifier les 3 lignes ensemble (et la même dans build-winpe.sh).
WIMBOOT_VERSION="v2.9.0"
WIMBOOT_URL="https://github.com/ipxe/wimboot/releases/download/v2.9.0/wimboot"
WIMBOOT_SHA256="5f067ccdc4d084d5bf77b6c853bd0f8402dfc2b4cd1b103d358993ae97fae8e3"
# (si WIMBOOT_SHA256 est vide : # À VÉRIFIER — le script affiche l'empreinte obtenue et continue)

# ------------------------------------------------------------------ Outils ---
info() { printf '\n==> %s\n' "$*"; }
warn() { printf '\n/!\\ ATTENTION : %s\n' "$*" >&2; }
die()  { printf '\nERREUR : %s\n' "$*" >&2; exit 1; }
sha256_of() { sha256sum -- "$1" | cut -d' ' -f1; }

[[ $EUID -eq 0 ]] || die "Ce script doit être lancé en root (sudo -i, puis relancez-le)."

if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    [[ "${ID:-}" == "debian" && "${VERSION_ID:-}" == "12" ]] \
        || warn "Prévu pour Debian 12 ; ici : ${PRETTY_NAME:-inconnu}. On continue, mais rien n'est garanti."
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
case "$MINI_OS" in
    linux) BOOT_TEMPLATE="boot-linux.ipxe" ;;
    winpe) BOOT_TEMPLATE="boot.ipxe" ;;
    *) die "MINI_OS doit valoir « linux » ou « winpe » (reçu : « $MINI_OS »)." ;;
esac

for f in server/requirements.txt boot/boot.ipxe boot/boot-linux.ipxe boot/dnsmasq-tftp.conf \
         live/auto/config scripts/build-live.sh \
         deploy/mdtprox-api.service deploy/dnsmasq-tftp.override.conf server/.env.example; do
    [[ -f "$REPO_DIR/$f" ]] || die "Fichier introuvable dans le dépôt : $REPO_DIR/$f (dépôt incomplet ?)"
done

# Le dépôt doit vivre dans /opt/mdtprox (chemins écrits dans le service systemd).
if [[ "$REPO_DIR" != "$INSTALL_DIR" ]]; then
    [[ ! -e "$INSTALL_DIR" ]] \
        || die "Le dépôt est dans $REPO_DIR mais $INSTALL_DIR existe déjà. Lancez plutôt $INSTALL_DIR/scripts/setup-vm.sh."
    info "Copie du dépôt $REPO_DIR vers $INSTALL_DIR"
    cp -a "$REPO_DIR" "$INSTALL_DIR"
    REPO_DIR="$INSTALL_DIR"
fi

# --------------------------------------------- 1. Dossier TFTP + config TFTP ---
# Fait AVANT l'installation de dnsmasq : le service démarre alors directement en
# « TFTP seul » (sans DNS), au lieu de démarrer une 1re fois avec la config d'usine.
info "1/8 Dossier TFTP et configuration dnsmasq (TFTP seul)"
install -d -m 0755 -o root -g root "$TFTP_DIR" /etc/dnsmasq.d
install -m 0644 -o root -g root "$REPO_DIR/boot/dnsmasq-tftp.conf" /etc/dnsmasq.d/mdtprox-tftp.conf

# ---------------------------------------------------------- 2. Paquets Debian ---
info "2/8 Paquets Debian"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-venv dnsmasq ipxe wimtools p7zip-full curl ca-certificates \
    live-build debootstrap squashfs-tools syslinux syslinux-common pxelinux

# ------------------------------------------------------ 3. Utilisateur « mdt » ---
info "3/8 Utilisateur système « $SVC_USER »"
if ! id -u "$SVC_USER" >/dev/null 2>&1; then
    useradd --system --user-group --no-create-home --home-dir /nonexistent \
            --shell /usr/sbin/nologin "$SVC_USER"
fi

# --------------------------------------------------- 4. Environnement Python ---
info "4/8 Environnement Python (venv) et API"
if [[ ! -x "$INSTALL_DIR/.venv/bin/python" ]]; then
    python3 -m venv "$INSTALL_DIR/.venv"
fi
"$INSTALL_DIR/.venv/bin/pip" install --disable-pip-version-check --quiet \
    -r "$INSTALL_DIR/server/requirements.txt"

# ------------------------------- 5. /srv/deploy et fichiers TFTP (iPXE de Debian) ---
info "5/8 Dossiers de données et programmes de démarrage iPXE"
# DATA_DIR et images/ : l'API y écrit (journal, empreintes .sha256). winpe/ et live/ : elle ne fait que lire.
install -d -m 0755 -o "$SVC_USER" -g "$SVC_USER" "$DATA_DIR" "$DATA_DIR/images"
install -d -m 0755 -o root -g root "$DATA_DIR/winpe" "$DATA_DIR/live"
# snponly.efi : variante de secours pour un PC UEFI dont ipxe.efi ne reconnaît pas la carte réseau.
for f in ipxe.efi undionly.kpxe snponly.efi; do
    [[ -f "/usr/lib/ipxe/$f" ]] || die "/usr/lib/ipxe/$f est absent : le paquet « ipxe » est-il bien installé ?"
    install -m 0644 -o root -g root "/usr/lib/ipxe/$f" "$TFTP_DIR/$f"
done

# -------------------------------------------------------------- 6. wimboot ---
info "6/8 wimboot $WIMBOOT_VERSION"
WIMBOOT_DEST="$DATA_DIR/winpe/wimboot"
if [[ -f "$WIMBOOT_DEST" && -n "$WIMBOOT_SHA256" && "$(sha256_of "$WIMBOOT_DEST")" == "$WIMBOOT_SHA256" ]]; then
    echo "wimboot déjà présent et conforme : rien à faire."
else
    tmp="$(mktemp "$DATA_DIR/winpe/.wimboot.XXXXXX")"
    if ! curl -fsSL --retry 3 --connect-timeout 15 -o "$tmp" "$WIMBOOT_URL"; then
        rm -f -- "$tmp"
        die "Téléchargement impossible : $WIMBOOT_URL (la VM a-t-elle accès à Internet ?)"
    fi
    got="$(sha256_of "$tmp")"
    if [[ -z "$WIMBOOT_SHA256" ]]; then
        warn "# À VÉRIFIER : aucune empreinte épinglée pour wimboot. SHA-256 obtenue : $got (comparez-la à la source officielle)."
    elif [[ "$got" != "$WIMBOOT_SHA256" ]]; then
        rm -f -- "$tmp"
        die "Empreinte de wimboot inattendue (obtenue $got, attendue $WIMBOOT_SHA256). Fichier rejeté."
    fi
    chmod 0644 "$tmp"
    mv -f -- "$tmp" "$WIMBOOT_DEST"
    echo "wimboot installé : $WIMBOOT_DEST"
fi

# ---------------------------------------- 7. boot.ipxe, secrets, profils ---
info "7/8 boot.ipxe ($MINI_OS), $ENV_FILE et profiles.yaml"
BOOT_DEST="$DATA_DIR/boot.ipxe"
BOOT_SRC="$REPO_DIR/boot/$BOOT_TEMPLATE"
if [[ -f "$BOOT_DEST" ]] && ! cmp -s "$BOOT_SRC" "$BOOT_DEST"; then
    cp -p -- "$BOOT_DEST" "$BOOT_DEST.bak"
    echo "boot.ipxe existant différent de $BOOT_TEMPLATE : ancienne copie gardée dans $BOOT_DEST.bak"
fi
install -m 0644 -o root -g root "$BOOT_SRC" "$BOOT_DEST"

if [[ ! -e "$ENV_FILE" ]]; then
    install -m 0600 -o root -g root "$REPO_DIR/server/.env.example" "$ENV_FILE"
    warn "$ENV_FILE vient d'être créé à partir d'un EXEMPLE. Éditez-le (nano $ENV_FILE) : mots de passe réels (CHANGEZ-MOI), adresse du serveur, etc."
else
    chmod 0600 "$ENV_FILE"
    echo "$ENV_FILE existe déjà : conservé tel quel."
fi

PROFILES="$DATA_DIR/profiles.yaml"
if [[ ! -e "$PROFILES" ]]; then
    install -m 0640 -o "$SVC_USER" -g "$SVC_USER" "$REPO_DIR/server/profiles.example.yaml" "$PROFILES"
    warn "$PROFILES vient d'être créé à partir de l'EXEMPLE : ses codes de salle sont PUBLICS (écrits dans le dépôt). Remplacez-les (python3 $INSTALL_DIR/server/tools/hash_code.py) avant toute vraie utilisation."
else
    echo "$PROFILES existe déjà : conservé tel quel."
fi

# ----------------------------------------- 8. Services, pare-feu, démarrage ---
info "8/8 Services systemd, pare-feu, démarrage"
install -m 0644 -o root -g root "$REPO_DIR/deploy/mdtprox-api.service" /etc/systemd/system/mdtprox-api.service
install -d -m 0755 /etc/systemd/system/dnsmasq.service.d
install -m 0644 -o root -g root "$REPO_DIR/deploy/dnsmasq-tftp.override.conf" /etc/systemd/system/dnsmasq.service.d/mdtprox-tftp.conf
systemctl daemon-reload

dnsmasq --test --conf-file=/etc/dnsmasq.d/mdtprox-tftp.conf \
    || die "La configuration TFTP de dnsmasq est invalide (/etc/dnsmasq.d/mdtprox-tftp.conf)."

# Port de l'API lu dans le fichier d'environnement (sans le « sourcer » : il contient des mots de passe).
API_PORT="$(sed -n 's/^BIND_PORT="\{0,1\}\([0-9]\{1,5\}\)"\{0,1\}[[:space:]]*$/\1/p' "$ENV_FILE" | tail -n 1)"
API_PORT="${API_PORT:-8080}"

# Pare-feu minimal : uniquement si ufw est installé ET actif (on n'en active jamais un soi-même,
# pour ne pas se couper l'accès SSH).
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep '^Status: active' > /dev/null; then
    ufw allow "$API_PORT/tcp" >/dev/null
    ufw allow 69/udp >/dev/null
    echo "ufw : ports $API_PORT/tcp (API) et 69/udp (TFTP) ouverts."
else
    echo "Pas de pare-feu ufw actif : rien à ouvrir (si vous en ajoutez un : $API_PORT/tcp et 69/udp)."
fi

systemctl enable dnsmasq >/dev/null 2>&1 || true
if ! systemctl restart dnsmasq; then
    warn "dnsmasq (TFTP) ne démarre pas : voir « journalctl -u dnsmasq -n 30 »."
fi

systemctl enable mdtprox-api >/dev/null 2>&1
if grep -q 'CHANGEZ-MOI' "$ENV_FILE"; then
    warn "$ENV_FILE contient encore « CHANGEZ-MOI » : l'API n'est PAS démarrée (elle déploierait des PC avec de faux mots de passe). Éditez le fichier puis : systemctl restart mdtprox-api"
    API_STARTED=0
else
    systemctl restart mdtprox-api
    API_STARTED=1
fi

# ------------------------------------------------- Auto-vérifications (non bloquantes) ---
# On teste avec l'adresse réelle de la VM (et non 127.0.0.1) : sous Debian, dnsmasq peut être
# réglé pour ignorer l'interface « lo ».
info "Vérifications"
SELF_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
SELF_IP="${SELF_IP:-127.0.0.1}"
if curl -fsS --max-time 5 -o /dev/null "tftp://$SELF_IP/undionly.kpxe"; then
    echo "TFTP : OK (undionly.kpxe téléchargeable depuis $SELF_IP)"
else
    warn "TFTP : pas de réponse sur $SELF_IP (voir « journalctl -u dnsmasq »)."
fi
if [[ "$API_STARTED" -eq 1 ]]; then
    ok=0
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        if curl -fsS --max-time 2 -o /dev/null "http://$SELF_IP:$API_PORT/healthz"; then ok=1; break; fi
        sleep 1
    done
    if [[ "$ok" -eq 1 ]]; then
        echo "API : OK (http://$SELF_IP:$API_PORT/healthz répond)"
    else
        warn "L'API ne répond pas sur $SELF_IP:$API_PORT : voir « journalctl -u mdtprox-api -n 40 »."
    fi
fi

if [[ "$MINI_OS" == "linux" ]]; then
    IMAGES_STEP="3. Copier vos DOSSIERS d'images Clonezilla dans $DATA_DIR/images/
      (python3 $INSTALL_DIR/server/tools/scan_images.py $DATA_DIR/images écrit les lignes de profiles.yaml)"
    BUILD_STEP="4. Fabriquer le mini-système Linux (il manque encore vmlinuz, initrd.img, filesystem.squashfs) :
        $INSTALL_DIR/scripts/build-live.sh"
else
    IMAGES_STEP="3. Copier vos images Windows (.wim) dans $DATA_DIR/images/"
    BUILD_STEP="4. Fabriquer le mini-système WinPE (il manque encore boot.wim, BCD, boot.sdi, bootmgr) :
        $INSTALL_DIR/scripts/build-winpe.sh /chemin/Windows.iso http://<IP-de-cette-VM>:$API_PORT"
fi

cat <<EOF

============================================================================
 Installation terminée (mini-système : $MINI_OS). Il reste, dans l'ordre :
   1. Éditer $ENV_FILE         (mots de passe, URL ; puis systemctl restart mdtprox-api)
   2. Remplacer les codes de $PROFILES
   $IMAGES_STEP
   $BUILD_STEP
   5. Configurer le DHCP de l'établissement (lecture seule d'abord) : scripts/dhcp-pxe.ps1
============================================================================
EOF
