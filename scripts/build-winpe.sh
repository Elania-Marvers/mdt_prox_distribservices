#!/usr/bin/env bash
# =============================================================================
#  build-winpe.sh : fabrique le mini-Windows (WinPE) servi aux PC par le réseau.
#
#  Usage (sur la VM Debian, en root) :
#      build-winpe.sh  /chemin/vers/Windows.iso  http://10.0.0.3:8080  [DATA_DIR]
#
#    1. ISO Windows 10/11 OFFICIELLE (celle de l'établissement, fournie par vous) ;
#    2. URL du serveur d'API telle que les PC la verront (sans « / » final) ;
#    3. (facultatif) DATA_DIR, défaut : $DATA_DIR de l'environnement, sinon /srv/deploy.
#  Variables facultatives : SOURCE_INDEX=2 (image à reprendre dans boot.wim),
#  DRIVERS_DIR=/dossier (pilotes réseau .inf supplémentaires, copiés dans X:\deploy\drivers),
#  CACHE_DIR (où garder l'archive Python téléchargée).
#
#  !!! IMPORTANT : le boot.wim produit est FABRIQUE A PARTIR DU MEDIA WINDOWS DE
#  !!! L'UTILISATEUR (sous licence Microsoft). Il n'est JAMAIS publié et JAMAIS
#  !!! commité dans Git (le .gitignore exclut *.wim, wimboot, *.iso, *.esd…).
#
#  Ce que le script fait :
#    a. extrait de l'ISO (avec 7z) : sources/boot.wim, boot/bcd, boot/boot.sdi, bootmgr ;
#    b. exporte l'image « Windows Setup » (index 2) vers un WIM d'UNE seule image, marquée amorçable ;
#    c. y injecte (wimlib-imagex update) : le client (/deploy/client + client.json),
#       Python embarqué (/python, fichier ._pth adapté), startnet.cmd ; supprime winpeshl.ini
#       (sinon l'installeur Windows démarrerait à la place de notre programme) ;
#    d. dépose dans DATA_DIR/winpe/ : boot.wim, BCD, boot.sdi, bootmgr, wimboot.
#
#  UEFI : rien de plus à extraire de l'ISO. D'après le code de wimboot (efifile.c),
#  il prend lui-même le gestionnaire de démarrage UEFI (\Windows\Boot\EFI\bootmgfw.efi)
#  et, si besoin, le BCD UEFI DANS boot.wim ; le BCD « BIOS » est adapté à la volée
#  (.exe -> .efi). Le script vérifie que bootmgfw.efi est bien présent dans le WIM.
#
#  RELANÇABLE : tout est refait à chaque fois dans un dossier de travail temporaire
#  supprimé à la fin ; les fichiers finaux sont remplacés d'un coup (mv atomique),
#  un PC en train de télécharger l'ancien boot.wim n'est pas perturbé.
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------- Réglages ---
# Python « embeddable » (python.org) : version ÉPINGLÉE.
# 3.12.10 = dernière 3.12 publiée avec les binaires Windows ; la bibliothèque standard
# suffit au client. Vérifié le 2026-10-23 : SHA-256 calculée sur le fichier téléchargé
# depuis cette URL, et son MD5 (fe8ef205f2e9c3ba44d0cf9954e1abd3) est identique à celui
# affiché sur https://www.python.org/downloads/release/python-31210/
# Pour changer de version : modifier URL + SHA-256 ensemble.
PYTHON_VERSION="3.12.10"
PYTHON_URL="https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
PYTHON_SHA256="4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
# (si PYTHON_SHA256 est vide : # À VÉRIFIER — le script affiche l'empreinte obtenue et continue)

# wimboot : MÊME version/empreinte que dans setup-vm.sh (gardez les deux synchronisés).
WIMBOOT_URL="https://github.com/ipxe/wimboot/releases/download/v2.9.0/wimboot"
WIMBOOT_SHA256="5f067ccdc4d084d5bf77b6c853bd0f8402dfc2b4cd1b103d358993ae97fae8e3"

MIN_FREE_MB=4096                      # place libre exigée dans le dossier de travail
WORK_PARENT="/var/tmp"                # (pas /tmp : peut être en mémoire vive)
WORK_PREFIX="mdtprox-winpe"            # dossier de travail = /var/tmp/mdtprox-winpe.XXXXXX

# ------------------------------------------------------------------ Outils ---
info() { printf '\n==> %s\n' "$*"; }
warn() { printf '\n/!\\ ATTENTION : %s\n' "$*" >&2; }
die()  { printf '\nERREUR : %s\n' "$*" >&2; exit 1; }
sha256_of() { sha256sum -- "$1" | cut -d' ' -f1; }

usage() {   # affiche le commentaire d'en-tête de ce fichier
    awk 'NR > 2 && /^# =====/ { exit } NR > 2 { sub(/^# ? ?/, ""); print }' "${BASH_SOURCE[0]}"
    exit "${1:-1}"
}

[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && usage 0
[[ $# -ge 2 && $# -le 3 ]] || usage 1

ISO="$1"
SERVER_URL="$2"
DATA_DIR="${3:-${DATA_DIR:-/srv/deploy}}"
SOURCE_INDEX="${SOURCE_INDEX:-}"
DRIVERS_DIR="${DRIVERS_DIR:-}"
CACHE_DIR="${CACHE_DIR:-/var/cache/mdtprox}"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CLIENT_SRC="$REPO_DIR/client"
STARTNET_SRC="$REPO_DIR/boot/winpe/startnet.cmd"

# ------------------------------------------------------------ Vérifications ---
for cmd in 7z wimlib-imagex curl sha256sum tar awk sed df mktemp install; do
    command -v "$cmd" >/dev/null 2>&1 \
        || die "Commande introuvable : $cmd (sur Debian : apt-get install wimtools p7zip-full curl ; ou lancez scripts/setup-vm.sh)."
done
[[ -f "$ISO" && -r "$ISO" ]] || die "ISO introuvable ou illisible : $ISO"
[[ "$SERVER_URL" =~ ^https?://[A-Za-z0-9._-]+(:[0-9]{1,5})?$ ]] \
    || die "URL du serveur invalide : « $SERVER_URL ». Attendu : http://adresse:port, sans « / » final (ex. http://10.0.0.3:8080)."
[[ -f "$CLIENT_SRC/deploy_client.py" ]] || die "Client introuvable : $CLIENT_SRC/deploy_client.py"
[[ -f "$STARTNET_SRC" ]] || die "startnet.cmd introuvable : $STARTNET_SRC"
if [[ -n "$DRIVERS_DIR" ]]; then
    [[ -d "$DRIVERS_DIR" ]] || die "DRIVERS_DIR n'est pas un dossier : $DRIVERS_DIR"
fi
[[ -n "$DATA_DIR" && "$DATA_DIR" == /* ]] || die "DATA_DIR doit être un chemin absolu (reçu : « $DATA_DIR »)."

# wimlib compare les chemins en respectant la casse sous Linux : on veut l'inverse
# (« Windows\System32\startnet.cmd » existe peut-être en « StartNet.cmd »).
export WIMLIB_IMAGEX_IGNORE_CASE=1

# ----------------------------------------------- Dossier de travail (sûr) ---
WORK="$(mktemp -d "$WORK_PARENT/$WORK_PREFIX.XXXXXX")"
cleanup() {
    # N'efface QUE le dossier créé ci-dessus, et seulement s'il a bien le bon nom.
    if [[ -n "${WORK:-}" && "$WORK" == "$WORK_PARENT/$WORK_PREFIX."* && -d "$WORK" ]]; then
        rm -rf -- "$WORK"
    fi
}
trap cleanup EXIT

free_mb="$(df -Pm "$WORK" | awk 'NR==2 {print $4}')"
[[ "$free_mb" -ge "$MIN_FREE_MB" ]] \
    || die "Place insuffisante dans $WORK_PARENT : ${free_mb} Mo libres, ${MIN_FREE_MB} Mo nécessaires."

info "Fabrication du WinPE à partir de : $ISO"
echo "   Serveur écrit dans le client : $SERVER_URL"
echo "   Destination                  : $DATA_DIR/winpe/"
echo "   Dossier de travail           : $WORK (supprimé à la fin)"

# --------------------------------------------- 1. Python embarqué (cache) ---
info "1/6 Python embarqué $PYTHON_VERSION"
mkdir -p "$CACHE_DIR"
PY_ZIP="$CACHE_DIR/$(basename "$PYTHON_URL")"
if [[ -f "$PY_ZIP" && -n "$PYTHON_SHA256" && "$(sha256_of "$PY_ZIP")" == "$PYTHON_SHA256" ]]; then
    echo "Archive déjà en cache et conforme : $PY_ZIP"
else
    curl -fsSL --retry 3 --connect-timeout 15 -o "$WORK/python.zip" "$PYTHON_URL" \
        || die "Téléchargement impossible : $PYTHON_URL"
    got="$(sha256_of "$WORK/python.zip")"
    if [[ -z "$PYTHON_SHA256" ]]; then
        warn "# À VÉRIFIER : aucune empreinte épinglée pour Python. SHA-256 obtenue : $got (comparez-la à python.org)."
    elif [[ "$got" != "$PYTHON_SHA256" ]]; then
        die "Empreinte de l'archive Python inattendue (obtenue $got, attendue $PYTHON_SHA256). Fichier rejeté."
    fi
    mv -f -- "$WORK/python.zip" "$PY_ZIP"
fi

# ------------------------------------------------------ 2. wimboot (si absent) ---
info "2/6 wimboot"
mkdir -p "$DATA_DIR/winpe"
WIMBOOT_DEST="$DATA_DIR/winpe/wimboot"
if [[ -f "$WIMBOOT_DEST" && "$(sha256_of "$WIMBOOT_DEST")" == "$WIMBOOT_SHA256" ]]; then
    echo "wimboot déjà présent et conforme : $WIMBOOT_DEST"
else
    curl -fsSL --retry 3 --connect-timeout 15 -o "$WORK/wimboot" "$WIMBOOT_URL" \
        || die "Téléchargement impossible : $WIMBOOT_URL"
    [[ "$(sha256_of "$WORK/wimboot")" == "$WIMBOOT_SHA256" ]] \
        || die "Empreinte de wimboot inattendue. Fichier rejeté."
fi

# ------------------------------------------------ 3. Extraction depuis l'ISO ---
info "3/6 Extraction des fichiers de démarrage depuis l'ISO (7z)"
# On liste l'ISO puis on retrouve le chemin EXACT de chaque fichier sans tenir compte
# de la casse (boot/bcd ou Boot/BCD selon l'ISO).
LISTING="$WORK/iso.lst"
7z l -slt "$ISO" > "$LISTING" || die "7z ne sait pas lire cette ISO."
find_in_iso() {
    awk -v want="$1" 'BEGIN { w = tolower(want) }
        /^Path = / { p = substr($0, 8); if (tolower(p) == w) { print p; exit } }' "$LISTING"
}
WANTED=()
for f in sources/boot.wim boot/bcd boot/boot.sdi bootmgr; do
    exact="$(find_in_iso "$f")"
    [[ -n "$exact" ]] || die "« $f » est introuvable dans l'ISO : est-ce bien un média Windows 10/11 officiel ?"
    WANTED+=("$exact")
done
rc=0
7z x -y -o"$WORK/iso" "$ISO" "${WANTED[@]}" > "$WORK/7z-extract.log" 2>&1 || rc=$?
[[ "$rc" -le 1 ]] || { tail -n 15 "$WORK/7z-extract.log" >&2; die "Extraction impossible (7z code $rc)."; }
rm -f -- "$LISTING"

SRC_WIM="$WORK/iso/${WANTED[0]}"
SRC_BCD="$WORK/iso/${WANTED[1]}"
SRC_SDI="$WORK/iso/${WANTED[2]}"
SRC_BOOTMGR="$WORK/iso/${WANTED[3]}"
for f in "$SRC_WIM" "$SRC_BCD" "$SRC_SDI" "$SRC_BOOTMGR"; do
    [[ -s "$f" ]] || die "Fichier non extrait : $f"
done

# ----------------------------------------- 4. Export d'une image amorçable ---
info "4/6 Export de l'image « Windows Setup » en un WIM d'une seule image"
# On cherche l'image dont le nom contient « Setup » (index 2 sur les ISO Windows 10/11).
if [[ -z "$SOURCE_INDEX" ]]; then
    SOURCE_INDEX="$(wimlib-imagex info "$SRC_WIM" | awk '
        /^Index:/ { i = $2 }
        /^Name:/  { if (!fait && tolower($0) ~ /setup/) { print i; fait = 1 } }')"
    if [[ -z "$SOURCE_INDEX" ]]; then
        SOURCE_INDEX=2
        warn "Aucune image « Setup » trouvée par son nom : on prend l'index 2 (valeur habituelle). Images présentes :"
        wimlib-imagex info "$SRC_WIM" | grep -E '^(Index|Name):' >&2 || true
    fi
fi
echo "Image reprise de boot.wim : index $SOURCE_INDEX"

NEW_WIM="$WORK/mdtprox-winpe.wim"
wimlib-imagex export "$SRC_WIM" "$SOURCE_INDEX" "$NEW_WIM" \
    "MDTPROX WinPE" "Mini-systeme de deploiement MDTPROX (fabrique depuis le media Windows de l'etablissement)" \
    --boot
rm -f -- "$SRC_WIM"        # 600 Mo de moins sur le disque de travail

# Vrai si le fichier « $2 » figure dans le dossier « $1 » du WIM final.
# (pas de « grep -q » : avec pipefail il ferait échouer wimlib par SIGPIPE.)
wim_has() {
    wimlib-imagex dir "$NEW_WIM" 1 --path="$1" 2>/dev/null | grep -i -F -e "$2" > /dev/null
}

# Vérifie que wimboot trouvera de quoi démarrer en BIOS et en UEFI dans ce WIM.
wim_has /Windows/Boot/EFI bootmgfw.efi \
    || warn "bootmgfw.efi est absent du WIM (\\Windows\\Boot\\EFI) : le démarrage UEFI échouera probablement. Le BIOS reste possible."
wim_has /Windows/Boot/PXE bootmgr.exe \
    || warn "bootmgr.exe est absent du WIM (\\Windows\\Boot\\PXE) : en BIOS, c'est le fichier « bootmgr » de l'ISO qui sera utilisé."

# ---------------------------------------------------- 5. Préparation de l'injection ---
info "5/6 Préparation de ce qui sera injecté (client, Python, startnet.cmd)"
STAGE="$WORK/stage"
mkdir -p "$STAGE/client" "$STAGE/python"

# Client : tout le dossier client/, sans les tests ni les caches.
tar -C "$CLIENT_SRC" --exclude='__pycache__' --exclude='.pytest_cache' --exclude='tests' \
    --exclude='*.pyc' -cf - . | tar -C "$STAGE/client" -xf -
# client.json : adresse du serveur + valeurs par défaut du contrat (CDC, annexe A.8).
printf '{"server_url": "%s", "penalty_seconds": 90, "ascii_only": false}\n' "$SERVER_URL" \
    > "$STAGE/client/client.json"

# Python : on dézippe, puis on remplace le fichier ._pth. Sans cela, "import" ne
# trouverait pas les modules du client (X:\deploy\client). Chemins relatifs au ._pth.
7z x -y -o"$STAGE/python" "$PY_ZIP" > /dev/null
shopt -s nullglob
PTH_FILES=("$STAGE"/python/python*._pth)
shopt -u nullglob
[[ ${#PTH_FILES[@]} -eq 1 ]] || die "Fichier python*._pth introuvable (ou en double) dans l'archive Python."
[[ -f "$STAGE/python/python.exe" ]] || die "python.exe absent de l'archive Python."
PTH_STEM="$(basename "${PTH_FILES[0]}" ._pth)"       # ex. python312
printf '%s.zip\r\n.\r\n..\\deploy\\client\r\n' "$PTH_STEM" > "${PTH_FILES[0]}"

# startnet.cmd : fins de ligne Windows (CRLF) exigées par cmd.exe.
sed 's/\r$//; s/$/\r/' "$STARTNET_SRC" > "$STAGE/startnet.cmd"

# ----------------------------------------------- 6. Injection + mise en place ---
info "6/6 Injection dans le WIM (wimlib-imagex update) et installation"
{
    echo "# Commandes pour wimlib-imagex update (generees par build-winpe.sh)"
    echo "# winpeshl.ini lancerait le programme d installation de Windows : on le supprime,"
    echo "# WinPE execute alors startnet.cmd."
    echo "delete --force /Windows/System32/winpeshl.ini"
    echo "add \"$STAGE/startnet.cmd\" /Windows/System32/startnet.cmd"
    echo "add \"$STAGE/python\" /python"
    echo "add \"$STAGE/client\" /deploy/client"
    if [[ -n "$DRIVERS_DIR" ]]; then
        echo "add \"$DRIVERS_DIR\" /deploy/drivers"
    fi
} > "$WORK/update.cmd"
wimlib-imagex update "$NEW_WIM" 1 < "$WORK/update.cmd"

# Contrôles : ce dont le démarrage dépend est bien dans le WIM, et winpeshl.ini n'y est plus.
for p in /python/python.exe /deploy/client/deploy_client.py /deploy/client/client.json /Windows/System32/startnet.cmd; do
    wim_has "$(dirname "$p")" "$(basename "$p")" || die "Contrôle : $p est absent du WIM final."
done
if wim_has /Windows/System32 winpeshl.ini; then
    die "Contrôle : winpeshl.ini est encore dans le WIM (l'installeur Windows démarrerait)."
fi

# Mise en place : on copie sous un nom temporaire dans le dossier de destination,
# puis on renomme (atomique, même disque).
install_atomic() {   # $1 = source, $2 = nom final dans winpe/
    local dest="$DATA_DIR/winpe/$2"
    install -m 0644 "$1" "$dest.new"
    mv -f -- "$dest.new" "$dest"
}
install_atomic "$NEW_WIM"     boot.wim
install_atomic "$SRC_BCD"     BCD
install_atomic "$SRC_SDI"     boot.sdi
install_atomic "$SRC_BOOTMGR" bootmgr
if [[ ! -f "$WIMBOOT_DEST" || "$(sha256_of "$WIMBOOT_DEST")" != "$WIMBOOT_SHA256" ]]; then
    install_atomic "$WORK/wimboot" wimboot
fi

echo
echo "============================================================================"
echo " WinPE prêt dans $DATA_DIR/winpe/ :"
find "$DATA_DIR/winpe" -maxdepth 1 -type f ! -name '.*' -printf '   %10s octets  %f\n'
echo
echo " boot.wim SHA-256 : $(sha256_of "$DATA_DIR/winpe/boot.wim")"
echo " Ce WIM contient du Windows (licence Microsoft) : ne le publiez pas, ne le"
echo " commitez pas. Vérification depuis un PC du réseau :"
echo "   curl -I $SERVER_URL/winpe/boot.wim"
echo "============================================================================"
