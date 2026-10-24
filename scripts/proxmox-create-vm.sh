#!/usr/bin/env bash
# =============================================================================
#  proxmox-create-vm.sh : crée la VM Debian 12 qui hébergera le service MDT.
#
#  À lancer SUR L'HÔTE PROXMOX (en root, dans « Shell » de l'interface web ou en SSH) :
#      ./proxmox-create-vm.sh  /root/ma-cle.pub
#  Le seul argument est le FICHIER de votre clé SSH PUBLIQUE (ssh-ed25519 …, ssh-rsa …) :
#  elle sera installée dans la VM (connexion par clé, pas de mot de passe).
#
#  Ce que le script fait : télécharge l'image « cloud » officielle de Debian 12
#  (vérifiée par SHA-512), crée la VM, y importe le disque, règle cloud-init
#  (IP fixe, passerelle, DNS, clé SSH) puis S'ARRÊTE. Il ne DÉMARRE PAS la VM.
#
#  GARANTIES :
#   - il refuse de continuer si le numéro de VM existe déjà (rien n'est modifié ni détruit) ;
#   - il refuse de continuer si l'adresse IP de la VM répond déjà au ping (voir ci-dessous) ;
#   - aucune commande de suppression.
#
#  ADRESSE IP : Si cette adresse est celle d'un ANCIEN serveur de déploiement, ne la donner
#  à la VM qu'APRÈS avoir ARRÊTÉ (et réinstallé) cet ancien serveur : deux machines
#  avec la même adresse = coupures de réseau pour tout le monde. L'hôte Proxmox
#  lui-même doit avoir sa PROPRE adresse, différente de celle de la VM
#  (à fixer avec le service réseau).
#
#  Tout se règle par les variables ci-dessous, ou à l'appel :
#      VMID=120 DISK_GB=100 ./proxmox-create-vm.sh /root/ma-cle.pub
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------- Réglages ---
VMID="${VMID:-}"                       # vide = prochain numéro libre choisi par Proxmox
VM_NAME="${VM_NAME:-mdtprox}"
CORES="${CORES:-2}"
MEMORY_MB="${MEMORY_MB:-4096}"
DISK_GB="${DISK_GB:-200}"              # taille finale du disque de la VM
STORAGE="${STORAGE:-local-lvm}"        # stockage Proxmox du disque : # À RELEVER (commande « pvesm status »)
BRIDGE="${BRIDGE:-vmbr0}"              # pont réseau de l'hôte
VLAN_TAG="${VLAN_TAG:-}"               # vide = pas d'étiquette VLAN. # À RELEVER : le port du switch est-il en « trunk » ?

VM_IP="${VM_IP:-10.0.0.3/24}"      # adresse de la VM (voir l'avertissement ci-dessus)
VM_GW="${VM_GW:-10.0.0.254}"       # passerelle du VLAN serveurs
VM_DNS="${VM_DNS:-10.0.0.2}"       # serveur DNS
CI_USER="${CI_USER:-debian}"           # compte créé dans la VM (accès sudo, par clé SSH)

IMAGE_DIR_URL="https://cloud.debian.org/images/cloud/bookworm/latest"
IMAGE_FILE="debian-12-genericcloud-amd64.qcow2"

# ------------------------------------------------------------------ Outils ---
info() { printf '\n==> %s\n' "$*"; }
warn() { printf '\n/!\\ ATTENTION : %s\n' "$*" >&2; }
die()  { printf '\nERREUR : %s\n' "$*" >&2; exit 1; }

# --------------------------------------------------------- Vérifications ---
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then   # affiche le commentaire d'en-tête de ce fichier
    awk 'NR > 2 && /^# =====/ { exit } NR > 2 { sub(/^# ? ?/, ""); print }' "${BASH_SOURCE[0]}"
    exit 0
fi
[[ $# -eq 1 ]] || die "Il faut un argument : le fichier de la clé SSH publique. Exemple : $0 /root/ma-cle.pub"
SSH_KEY_FILE="$1"

[[ $EUID -eq 0 ]] || die "À lancer en root sur l'hôte Proxmox."
for cmd in qm pvesh pvesm curl sha512sum ping; do
    command -v "$cmd" >/dev/null 2>&1 || die "Commande introuvable : $cmd. Ce script se lance SUR l'hôte Proxmox."
done

[[ -f "$SSH_KEY_FILE" && -r "$SSH_KEY_FILE" ]] || die "Clé SSH publique introuvable : $SSH_KEY_FILE"
grep -Eq '^(ssh-(ed25519|rsa)|ecdsa-sha2-|sk-)' "$SSH_KEY_FILE" \
    || die "$SSH_KEY_FILE ne ressemble pas à une clé SSH PUBLIQUE (doit commencer par ssh-ed25519, ssh-rsa…). N'envoyez jamais la clé privée."

[[ "$VM_IP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$ ]] || die "VM_IP doit avoir la forme 10.0.0.3/24 (reçu : « $VM_IP »)."
VM_ADDR="${VM_IP%/*}"
[[ "$DISK_GB" =~ ^[0-9]+$ && "$CORES" =~ ^[0-9]+$ && "$MEMORY_MB" =~ ^[0-9]+$ ]] \
    || die "DISK_GB, CORES et MEMORY_MB doivent être des nombres entiers."

# Numéro de VM : jamais celui d'une VM/conteneur existant (sur tout le cluster).
if [[ -z "$VMID" ]]; then
    VMID="$(pvesh get /cluster/nextid)"
fi
[[ "$VMID" =~ ^[0-9]+$ ]] || die "Numéro de VM invalide : « $VMID »."
if qm status "$VMID" >/dev/null 2>&1 \
   || [[ -e "/etc/pve/qemu-server/$VMID.conf" || -e "/etc/pve/lxc/$VMID.conf" ]] \
   || pvesh get /cluster/resources --type vm --output-format json 2>/dev/null \
        | grep -E "\"vmid\" *: *$VMID([^0-9]|\$)" > /dev/null; then
    die "Le numéro $VMID existe déjà (VM ou conteneur). Rien n'a été modifié. Choisissez-en un autre : VMID=NNN $0 $SSH_KEY_FILE"
fi

pvesm status --storage "$STORAGE" >/dev/null 2>&1 \
    || die "Stockage « $STORAGE » inconnu. Liste des stockages : pvesm status   (puis STORAGE=nom $0 …)"

# Garde-fou : l'adresse IP est-elle déjà occupée ?
if ping -c 2 -W 1 "$VM_ADDR" >/dev/null 2>&1; then
    die "L'adresse $VM_ADDR répond déjà au ping (ancien serveur de déploiement encore allumé ?). Arrêtez cette machine d'abord, ou choisissez une autre adresse (VM_IP=…)."
fi

# ------------------------------------------------------------- Image Debian ---
TMP="$(mktemp -d /var/tmp/mdtprox-vm.XXXXXX)"
cleanup() {
    # N'efface QUE le dossier temporaire créé ci-dessus, et seulement s'il a le bon nom.
    if [[ -n "${TMP:-}" && "$TMP" == /var/tmp/mdtprox-vm.* && -d "$TMP" ]]; then
        rm -rf -- "$TMP"
    fi
}
trap cleanup EXIT

info "Téléchargement de l'image Debian 12 (cloud) : $IMAGE_FILE"
curl -fSL --retry 3 --connect-timeout 15 -o "$TMP/$IMAGE_FILE" "$IMAGE_DIR_URL/$IMAGE_FILE" \
    || die "Téléchargement impossible : $IMAGE_DIR_URL/$IMAGE_FILE (l'hôte Proxmox a-t-il accès à Internet ?)"
curl -fsSL --retry 3 --connect-timeout 15 -o "$TMP/SHA512SUMS" "$IMAGE_DIR_URL/SHA512SUMS" \
    || die "Téléchargement impossible : $IMAGE_DIR_URL/SHA512SUMS"
( cd "$TMP" && grep -F " $IMAGE_FILE" SHA512SUMS | grep -E " $IMAGE_FILE\$" | sha512sum -c --status - ) \
    || die "L'empreinte SHA-512 de l'image ne correspond pas : téléchargement corrompu, abandon."
echo "Image vérifiée (SHA-512 conforme à la liste officielle de Debian)."

# ---------------------------------------------------------------- Création VM ---
info "Création de la VM $VMID ($VM_NAME) : $CORES vCPU, $MEMORY_MB Mo, disque $DISK_GB Go, pont $BRIDGE"
NET0="virtio,bridge=$BRIDGE"
[[ -z "$VLAN_TAG" ]] || NET0="$NET0,tag=$VLAN_TAG"

qm create "$VMID" \
    --name "$VM_NAME" \
    --ostype l26 \
    --cores "$CORES" \
    --memory "$MEMORY_MB" \
    --net0 "$NET0" \
    --scsihw virtio-scsi-single \
    --serial0 socket \
    --onboot 1 \
    --description "Service MDT de l'etablissement (API + TFTP). Cree par proxmox-create-vm.sh. Installer ensuite avec scripts/setup-vm.sh."

info "Import du disque dans le stockage $STORAGE"
if qm disk import --help >/dev/null 2>&1; then
    qm disk import "$VMID" "$TMP/$IMAGE_FILE" "$STORAGE"
else
    qm importdisk "$VMID" "$TMP/$IMAGE_FILE" "$STORAGE"      # anciennes versions de Proxmox
fi
DISK_REF="$(qm config "$VMID" | sed -n 's/^unused0: //p')"
[[ -n "$DISK_REF" ]] || die "Disque importé introuvable dans la configuration de la VM $VMID (voir « qm config $VMID »)."

qm set "$VMID" --scsi0 "$DISK_REF,discard=on,iothread=1,ssd=1"
qm set "$VMID" --boot order=scsi0
qm resize "$VMID" scsi0 "${DISK_GB}G"

info "Réglage cloud-init (utilisateur $CI_USER, IP $VM_IP, passerelle $VM_GW, DNS $VM_DNS)"
qm set "$VMID" \
    --ide2 "$STORAGE:cloudinit" \
    --ciuser "$CI_USER" \
    --sshkeys "$SSH_KEY_FILE" \
    --ipconfig0 "ip=$VM_IP,gw=$VM_GW" \
    --nameserver "$VM_DNS"

cat <<EOF

============================================================================
 VM $VMID « $VM_NAME » créée. Elle n'est PAS démarrée.

 Avant de la démarrer :
   - l'ancien serveur de déploiement doit être ARRÊTÉ si $VM_ADDR est son adresse ;
   - la VM n'a pas de mot de passe : on s'y connecte par clé SSH.

 Ensuite :
   1. qm start $VMID
   2. (attendre ~1 minute)   ssh $CI_USER@$VM_ADDR
   3. dans la VM :  sudo -i
                    apt-get install -y git
                    git clone <URL-du-dépôt> /opt/mdtprox
                    /opt/mdtprox/scripts/setup-vm.sh
============================================================================
EOF
