<#
.SYNOPSIS
    Prépare le DHCP de l'établissement (Windows Server) pour le démarrage réseau des PC (PXE -> iPXE -> serveur de déploiement).

.DESCRIPTION
    À LANCER SUR LE SERVEUR DHCP, dans PowerShell « en tant qu'administrateur » (compte membre du groupe
    « Administrateurs DHCP »). Module utilisé : DhcpServer (déjà présent sur un serveur DHCP).

    TROIS MODES, du plus prudent au plus actif :

    1. SANS option (par défaut) : LECTURE SEULE. N'écrit RIEN. Affiche les étendues (une par VLAN),
       les options 66/67 actuellement configurées (serveur, étendue, politiques), les classes et les
       politiques existantes. C'est ce qui permet de relever les valeurs aujourd'hui inconnues.

    2. -Apply : crée, pour l'étendue indiquée (à répéter pour chaque VLAN de salle) :
         - des classes constructeur  « PXEClient:Arch:00007 » (UEFI x64), « PXEClient:Arch:00009 »
           (UEFI x64, autre code) et « PXEClient:Arch:00000 » (BIOS) ;
         - une classe utilisateur « iPXE » (iPXE s'annonce ainsi) ;
         - 4 POLITIQUES d'étendue (les options ne s'appliquent qu'aux PC concernés, rien d'autre ne change) :
             ordre 1  iPXE        -> option 67 = http://<ServerIp>:<HttpPort>/boot.ipxe
             ordre 2  UEFI x64    -> option 66 = <ServerIp>, option 67 = ipxe.efi
             ordre 3  UEFI x64 bis-> option 66 = <ServerIp>, option 67 = ipxe.efi
             ordre 4  BIOS        -> option 66 = <ServerIp>, option 67 = undionly.kpxe
         La politique iPXE est placée AVANT les autres : un iPXE annonce aussi « PXEClient:Arch:… », donc
         il correspond à deux politiques ; c'est la plus prioritaire (numéro le plus petit) qui doit
         gagner pour l'option 67, sinon iPXE se rechargerait lui-même sans fin (boucle).
       Une confirmation « OUI » est demandée avant toute modification (sauf avec -WhatIf).

    3. -Remove : retire les 4 politiques de l'étendue, puis les classes créées par ce script
       (et seulement celles-là : leur description porte la marque [MDTPROX]).

    -WhatIf (avec -Apply ou -Remove) : montre ce qui serait fait, sans rien modifier.
    Le script est RELANÇABLE : ce qui existe déjà est mis à jour, pas dupliqué.

    RIEN n'est appliqué automatiquement : la modification du DHCP de production se décide ici,
    avec -Apply, après lecture de l'état actuel.

.PARAMETER Apply
    Active le mode « appliquer ». Exige -ScopeId et -ServerIp.

.PARAMETER Remove
    Active le mode « défaire ». Exige -ScopeId.

.PARAMETER ScopeId
    Identifiant de l'étendue = adresse du réseau du VLAN, ex. 10.0.10.0 (valeur affichée par le
    mode lecture seule : colonne ScopeId). Plusieurs valeurs possibles, séparées par des virgules.
    # À RELEVER : étendues des VLAN des salles (inconnues à ce jour).

.PARAMETER ServerIp
    Adresse IP du serveur de déploiement (TFTP + API). Si elle est celle d'un ancien serveur,
    ne l'utiliser qu'après l'arrêt de celui-ci.

.PARAMETER HttpPort
    Port de l'API de la VM (défaut 8080, voir BIND_PORT dans /etc/mdtprox.env).

.EXAMPLE
    .\dhcp-pxe.ps1
    Lecture seule : affiche l'état actuel du DHCP.

.EXAMPLE
    .\dhcp-pxe.ps1 -Apply -ScopeId 10.0.10.0 -ServerIp 10.0.0.3 -WhatIf
    Montre ce qui serait créé pour cette étendue, sans rien modifier.

.EXAMPLE
    .\dhcp-pxe.ps1 -Apply -ScopeId 10.0.10.0,10.0.11.0 -ServerIp 10.0.0.3

.EXAMPLE
    .\dhcp-pxe.ps1 -Remove -ScopeId 10.0.10.0
#>
#Requires -Version 5.1
#Requires -Modules DhcpServer

[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium', DefaultParameterSetName = 'Lecture')]
param(
    [Parameter(ParameterSetName = 'Appliquer', Mandatory = $true)]
    [switch]$Apply,

    [Parameter(ParameterSetName = 'Retirer', Mandatory = $true)]
    [switch]$Remove,

    [Parameter(ParameterSetName = 'Appliquer', Mandatory = $true)]
    [Parameter(ParameterSetName = 'Retirer', Mandatory = $true)]
    [IPAddress[]]$ScopeId,

    [Parameter(ParameterSetName = 'Appliquer', Mandatory = $true)]
    [IPAddress]$ServerIp,

    [Parameter(ParameterSetName = 'Appliquer')]
    [ValidateRange(1, 65535)]
    [int]$HttpPort = 8080
)

$ErrorActionPreference = 'Stop'

# Marque écrite dans la description de ce que le script crée : sert à ne retirer QUE cela.
$Marker = '[MDTPROX]'

# Politiques gérées par ce script (nom -> pour le mode -Remove et l'affichage).
$PolicyNames = @(
    'MDTPROX-PXE-1-iPXE',
    'MDTPROX-PXE-2-UEFI-x64',
    'MDTPROX-PXE-3-UEFI-x64-bis',
    'MDTPROX-PXE-4-BIOS'
)

# Classes gérées par ce script. Le nom est égal aux données envoyées par le PC.
$ClassDefs = @(
    @{ Name = 'PXEClient:Arch:00007'; Type = 'Vendor'; Data = 'PXEClient:Arch:00007'; Desc = "PC UEFI x64 en PXE $Marker" },
    @{ Name = 'PXEClient:Arch:00009'; Type = 'Vendor'; Data = 'PXEClient:Arch:00009'; Desc = "PC UEFI x64 en PXE (code alternatif) $Marker" },
    @{ Name = 'PXEClient:Arch:00000'; Type = 'Vendor'; Data = 'PXEClient:Arch:00000'; Desc = "PC BIOS classique en PXE $Marker" },
    @{ Name = 'iPXE';                 Type = 'User';   Data = 'iPXE';                 Desc = "Client iPXE $Marker" }
)

# ------------------------------------------------------------------ Affichage ---
function Write-Titre([string]$Texte) {
    Write-Host ''
    Write-Host "=== $Texte ===" -ForegroundColor Cyan
}

# Texte des données d'une classe (selon la version, la propriété s'appelle AsciiData ou seul Data existe).
function Get-ClassAscii($Classe) {
    try {
        if ($Classe.PSObject.Properties['AsciiData'] -and $Classe.AsciiData) { return [string]$Classe.AsciiData }
        if ($Classe.Data) { return [System.Text.Encoding]::ASCII.GetString([byte[]]$Classe.Data) }
    } catch { }
    return ''
}

# ----------------------------------------------------------- Mode 1 : lecture ---
function Show-Etat {
    Write-Host 'MODE LECTURE SEULE : rien n''est modifié.' -ForegroundColor Green

    Write-Titre 'Serveur DHCP'
    try { Get-DhcpServerSetting | Format-List IsAuthorized, ActivatePolicies | Out-Host } catch { Write-Warning $_.Exception.Message }

    Write-Titre 'Étendues IPv4 (normalement une par VLAN) -> colonne ScopeId à utiliser avec -ScopeId'
    $scopes = @(Get-DhcpServerv4Scope)
    if ($scopes.Count -eq 0) { Write-Host '  (aucune étendue)' }
    else {
        $scopes | Format-Table ScopeId, Name, SubnetMask, StartRange, EndRange, State, ActivatePolicies, LeaseDuration -AutoSize | Out-Host
    }

    Write-Titre 'Options 66 / 67 au niveau du SERVEUR (valables pour toutes les étendues sans valeur plus précise)'
    foreach ($id in 66, 67) {
        Get-DhcpServerv4OptionValue -OptionId $id -ErrorAction SilentlyContinue | Format-Table OptionId, Name, Value -AutoSize | Out-Host
    }

    foreach ($s in $scopes) {
        Write-Titre "Étendue $($s.ScopeId) - $($s.Name)"
        Write-Host '  Options 66 / 67 de l''étendue :'
        $trouve = $false
        foreach ($id in 66, 67) {
            $v = Get-DhcpServerv4OptionValue -ScopeId $s.ScopeId -OptionId $id -ErrorAction SilentlyContinue
            if ($v) { $trouve = $true; $v | Format-Table OptionId, Name, Value -AutoSize | Out-Host }
        }
        if (-not $trouve) { Write-Host '    (aucune)' }

        Write-Host '  Politiques de l''étendue (triées par ordre de traitement) :'
        $pols = @(Get-DhcpServerv4Policy -ScopeId $s.ScopeId -ErrorAction SilentlyContinue | Sort-Object ProcessingOrder)
        if ($pols.Count -eq 0) { Write-Host '    (aucune)' }
        else {
            $pols | Format-Table Name, ProcessingOrder, Enabled, Condition,
                @{ n = 'VendorClass'; e = { $_.VendorClass -join ' ' } },
                @{ n = 'UserClass';   e = { $_.UserClass -join ' ' } } -AutoSize | Out-Host
            foreach ($p in $pols) {
                foreach ($id in 66, 67) {
                    $v = Get-DhcpServerv4OptionValue -ScopeId $s.ScopeId -PolicyName $p.Name -OptionId $id -ErrorAction SilentlyContinue
                    if ($v) { Write-Host ("    politique '{0}' : option {1} = {2}" -f $p.Name, $id, ($v.Value -join ',')) }
                }
            }
        }
    }

    Write-Titre 'Classes constructeur / utilisateur définies sur le serveur'
    Get-DhcpServerv4Class | Format-Table Name, Type, Description -AutoSize | Out-Host

    Write-Host ''
    Write-Host 'Pour simuler  : .\dhcp-pxe.ps1 -Apply -ScopeId <étendue> -ServerIp <IP de la VM> -WhatIf'
    Write-Host 'Pour appliquer: .\dhcp-pxe.ps1 -Apply -ScopeId <étendue> -ServerIp <IP de la VM>'
}

# ----------------------------------------------------------- Mode 2 : appliquer ---
# Crée la classe si elle n'existe pas. Renvoie le NOM de la classe à utiliser dans les politiques
# (une classe équivalente déjà définie, même sous un autre nom, est réutilisée).
function Initialize-Classe([hashtable]$Def) {
    $existantes = @(Get-DhcpServerv4Class | Where-Object { $_.Type -eq $Def.Type })
    $memeDonnee = $existantes | Where-Object { (Get-ClassAscii $_) -ceq $Def.Data } | Select-Object -First 1
    if ($memeDonnee) {
        Write-Host ("  classe {0} '{1}' : déjà présente (réutilisée)" -f $Def.Type, $memeDonnee.Name)
        return $memeDonnee.Name
    }
    $memeNom = $existantes | Where-Object { $_.Name -eq $Def.Name } | Select-Object -First 1
    if ($memeNom) {
        throw "Une classe $($Def.Type) nommée '$($Def.Name)' existe déjà avec d'autres données. Renommez-la ou supprimez-la, puis relancez."
    }
    if ($PSCmdlet.ShouldProcess("classe $($Def.Type) '$($Def.Name)' (données : $($Def.Data))", 'Add-DhcpServerv4Class')) {
        try {
            Add-DhcpServerv4Class -Name $Def.Name -Type $Def.Type -Data $Def.Data -Description $Def.Desc
        } catch {
            throw "Création de la classe '$($Def.Name)' impossible : $($_.Exception.Message). Si une classe équivalente existe déjà sous un autre nom, relevez-la avec le mode lecture seule."
        }
        Write-Host ("  classe {0} '{1}' : créée" -f $Def.Type, $Def.Name)
    }
    return $Def.Name
}

# Crée la politique (ou la met à jour si elle existe déjà), puis ses options.
function Initialize-Politique($Scope, [string]$Nom, [int]$Ordre, [string]$Genre, [string]$NomClasse, [hashtable]$Options) {
    $condition = @{}
    if ($Genre -eq 'User') { $condition.UserClass = @('EQ', $NomClasse) }
    else                   { $condition.VendorClass = @('EQ', ($NomClasse + '*')) }   # « * » = commence par (le PC ajoute :UNDI:…)

    $cible = "politique '$Nom' dans l'étendue $Scope (ordre $Ordre)"
    $existe = Get-DhcpServerv4Policy -ScopeId $Scope -Name $Nom -ErrorAction SilentlyContinue
    if ($existe) {
        if ($PSCmdlet.ShouldProcess($cible, 'Set-DhcpServerv4Policy (mise à jour)')) {
            Set-DhcpServerv4Policy -ScopeId $Scope -Name $Nom -Condition OR -ProcessingOrder $Ordre -Enabled $true @condition
            Write-Host "  $cible : mise à jour"
        }
    } else {
        if ($PSCmdlet.ShouldProcess($cible, 'Add-DhcpServerv4Policy')) {
            Add-DhcpServerv4Policy -ScopeId $Scope -Name $Nom -Condition OR -ProcessingOrder $Ordre `
                -Description "Démarrage réseau MDT $Marker" @condition
            Write-Host "  $cible : créée"
        }
    }
    foreach ($id in ($Options.Keys | Sort-Object)) {
        if ($PSCmdlet.ShouldProcess("$cible : option $id = $($Options[$id])", 'Set-DhcpServerv4OptionValue')) {
            Set-DhcpServerv4OptionValue -ScopeId $Scope -PolicyName $Nom -OptionId $id -Value $Options[$id]
        }
    }
}

function Invoke-Appliquer {
    if ($ServerIp.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
        throw '-ServerIp doit être une adresse IPv4.'
    }
    $ip = $ServerIp.ToString()
    $urlIpxe = "http://${ip}:${HttpPort}/boot.ipxe"

    # Les étendues doivent exister.
    $etendues = foreach ($s in $ScopeId) { Get-DhcpServerv4Scope -ScopeId $s }

    Write-Host 'Ce script va, sur CE serveur DHCP :' -ForegroundColor Yellow
    Write-Host "  - créer/réutiliser 3 classes constructeur (PXEClient:Arch:00007/00009/00000) et la classe utilisateur iPXE ;"
    Write-Host "  - pour chaque étendue ci-dessous, créer/mettre à jour 4 politiques (options 66/67 réservées aux PC en PXE) :"
    foreach ($e in $etendues) { Write-Host ("      {0}  ({1})" -f $e.ScopeId, $e.Name) }
    Write-Host "  - PC iPXE -> option 67 = $urlIpxe"
    Write-Host "  - PC UEFI -> option 66 = $ip, option 67 = ipxe.efi   |   PC BIOS -> option 66 = $ip, option 67 = undionly.kpxe"
    Write-Host '  Les autres options et les autres PC ne sont pas touchés. Défaire : -Remove.' -ForegroundColor Yellow
    foreach ($e in $etendues) {
        $autres = @(Get-DhcpServerv4Policy -ScopeId $e.ScopeId -ErrorAction SilentlyContinue | Where-Object { $PolicyNames -notcontains $_.Name })
        if ($autres.Count -gt 0) {
            Write-Warning ("L'étendue {0} a déjà {1} autre(s) politique(s) : {2}. Vérifiez leur ordre de traitement (le nôtre : 1 à 4) avec le mode lecture seule." -f $e.ScopeId, $autres.Count, (($autres | ForEach-Object { $_.Name }) -join ', '))
        }
    }

    if (-not $WhatIfPreference) {
        $rep = Read-Host 'Appliquer ces changements au DHCP de production ? Tapez OUI en majuscules pour continuer'
        if ($rep -cne 'OUI') { Write-Host 'Annulé : rien n''a été modifié.'; return }
    }

    Write-Titre 'Classes (valables pour tout le serveur)'
    $nomClasse = @{}
    foreach ($def in $ClassDefs) { $nomClasse[$def.Data] = Initialize-Classe $def }

    foreach ($e in $etendues) {
        $s = $e.ScopeId
        Write-Titre "Étendue $s - $($e.Name)"
        # Sans « politiques activées », l'étendue ignorerait nos politiques.
        if (-not $e.ActivatePolicies) {
            if ($PSCmdlet.ShouldProcess("étendue $s", 'Set-DhcpServerv4Scope -ActivatePolicies $true')) {
                Set-DhcpServerv4Scope -ScopeId $s -ActivatePolicies $true
                Write-Host '  politiques activées sur l''étendue'
            }
        }
        # Ordre = priorité : iPXE d'abord (1), puis les types d'architecture.
        Initialize-Politique $s $PolicyNames[0] 1 'User'   $nomClasse['iPXE']                 @{ 67 = $urlIpxe }
        Initialize-Politique $s $PolicyNames[1] 2 'Vendor' $nomClasse['PXEClient:Arch:00007'] @{ 66 = $ip; 67 = 'ipxe.efi' }
        Initialize-Politique $s $PolicyNames[2] 3 'Vendor' $nomClasse['PXEClient:Arch:00009'] @{ 66 = $ip; 67 = 'ipxe.efi' }
        Initialize-Politique $s $PolicyNames[3] 4 'Vendor' $nomClasse['PXEClient:Arch:00000'] @{ 66 = $ip; 67 = 'undionly.kpxe' }
    }

    if (-not $WhatIfPreference) {
        Write-Titre 'Résultat : politiques des étendues traitées'
        foreach ($e in $etendues) {
            Write-Host "Étendue $($e.ScopeId)"
            Get-DhcpServerv4Policy -ScopeId $e.ScopeId | Sort-Object ProcessingOrder |
                Format-Table Name, ProcessingOrder, Enabled,
                    @{ n = 'VendorClass'; e = { $_.VendorClass -join ' ' } },
                    @{ n = 'UserClass';   e = { $_.UserClass -join ' ' } } -AutoSize | Out-Host
        }
        Write-Host 'À VÉRIFIER SUR BANC : démarrer un PC en PXE (BIOS puis UEFI) et contrôler qu''il charge ipxe.efi / undionly.kpxe, puis boot.ipxe (pas de boucle).' -ForegroundColor Yellow
    }
}

# ------------------------------------------------------------ Mode 3 : défaire ---
function Invoke-Retirer {
    Write-Host ('Ce script va retirer, des étendues ' + (($ScopeId | ForEach-Object { $_.ToString() }) -join ', ') + ', les 4 politiques MDTPROX-PXE-* puis les classes créées par lui.') -ForegroundColor Yellow
    if (-not $WhatIfPreference) {
        $rep = Read-Host 'Retirer ces éléments du DHCP de production ? Tapez OUI en majuscules pour continuer'
        if ($rep -cne 'OUI') { Write-Host 'Annulé : rien n''a été modifié.'; return }
    }

    foreach ($s in $ScopeId) {
        Write-Titre "Étendue $s"
        foreach ($nom in $PolicyNames) {
            if (Get-DhcpServerv4Policy -ScopeId $s -Name $nom -ErrorAction SilentlyContinue) {
                if ($PSCmdlet.ShouldProcess("politique '$nom' de l'étendue $s (et ses options)", 'Remove-DhcpServerv4Policy')) {
                    Remove-DhcpServerv4Policy -ScopeId $s -Name $nom
                    Write-Host "  politique '$nom' : retirée"
                }
            } else {
                Write-Host "  politique '$nom' : absente (rien à faire)"
            }
        }
    }

    Write-Titre 'Classes créées par ce script'
    $nous = @(Get-DhcpServerv4Class | Where-Object { $_.Description -and $_.Description.Contains($Marker) })
    if ($nous.Count -eq 0) { Write-Host '  (aucune)' }
    foreach ($c in $nous) {
        if ($PSCmdlet.ShouldProcess("classe $($c.Type) '$($c.Name)'", 'Remove-DhcpServerv4Class')) {
            try {
                Remove-DhcpServerv4Class -Name $c.Name -Type $c.Type
                Write-Host "  classe '$($c.Name)' : retirée"
            } catch {
                Write-Warning "Classe '$($c.Name)' laissée en place (encore utilisée par une politique d'une autre étendue ?) : $($_.Exception.Message)"
            }
        }
    }
    Write-Host ''
    Write-Host 'Les options 66/67 et les politiques qui existaient AVANT ce script n''ont pas été touchées.'
}

# ------------------------------------------------------------------ Principal ---
if ($Apply)       { Invoke-Appliquer }
elseif ($Remove)  { Invoke-Retirer }
else              { Show-Etat }
