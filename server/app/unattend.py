"""Génération de unattend.xml (Windows 10/11 amd64) et de SetupComplete.cmd.

Toutes les valeurs insérées dans le XML sont échappées (un mot de passe peut contenir & < > ' ").
"""
from string import Template
from typing import Optional
from xml.sax.saxutils import escape

_COMPONENT = ('processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" '
              'versionScope="nonSxS" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State" '
              'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"')

_TEMPLATE = Template("""<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend">
  <settings pass="specialize">
    <component name="Microsoft-Windows-Shell-Setup" $component>
      <ComputerName>$hostname</ComputerName>
      <TimeZone>$timezone</TimeZone>
    </component>
    <component name="Microsoft-Windows-UnattendedJoin" $component>
      <Identification>
        <Credentials>
          <Domain>$domain</Domain>
          <Username>$join_user</Username>
          <Password>$join_password</Password>
        </Credentials>
        <JoinDomain>$domain</JoinDomain>$ou_line
      </Identification>
    </component>
  </settings>
  <settings pass="oobeSystem">
    <component name="Microsoft-Windows-International-Core" $component>
      <InputLocale>$locale</InputLocale>
      <SystemLocale>$locale</SystemLocale>
      <UILanguage>$locale</UILanguage>
      <UserLocale>$locale</UserLocale>
    </component>
    <component name="Microsoft-Windows-Shell-Setup" $component>
      <OOBE>
        <HideEULAPage>true</HideEULAPage>
        <HideLocalAccountScreen>true</HideLocalAccountScreen>
        <HideOnlineAccountScreens>true</HideOnlineAccountScreens>
        <HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE>
        <ProtectYourPC>3</ProtectYourPC>
        <SkipMachineOOBE>true</SkipMachineOOBE>
        <SkipUserOOBE>true</SkipUserOOBE>
      </OOBE>
      <UserAccounts>
        <LocalAccounts>
          <LocalAccount wcm:action="add">
            <Name>$admin_user</Name>
            <Group>Administrators</Group>
            <Password>
              <Value>$admin_password</Value>
              <PlainText>true</PlainText>
            </Password>
          </LocalAccount>
        </LocalAccounts>
      </UserAccounts>
    </component>
  </settings>
</unattend>
""")


def _x(value: str) -> str:
    return escape(str(value), {'"': "&quot;", "'": "&apos;"})


def build_unattend(*, hostname: str, domain: str, join_user: str, join_password: str,
                   admin_user: str, admin_password: str, timezone: str, locale: str,
                   ou: Optional[str] = None) -> str:
    """Contenu de unattend.xml. Sans `ou`, la ligne MachineObjectOU est simplement omise."""
    ou_line = "\n        <MachineObjectOU>%s</MachineObjectOU>" % _x(ou) if ou else ""
    xml = _TEMPLATE.substitute(
        component=_COMPONENT, hostname=_x(hostname), timezone=_x(timezone), domain=_x(domain),
        join_user=_x(join_user), join_password=_x(join_password), ou_line=ou_line,
        locale=_x(locale), admin_user=_x(admin_user), admin_password=_x(admin_password))
    # Les caractères non ASCII (é, €…) deviennent &#233; : le fichier est lisible quel que soit
    # l'encodage utilisé par le client pour l'écrire.
    return xml.encode("ascii", "xmlcharrefreplace").decode("ascii")


def build_setup_complete() -> str:
    """SetupComplete.cmd : efface les fichiers de réponses (mots de passe en clair), puis lui-même."""
    lines = [
        "@echo off",
        "rem Supprime les fichiers de reponses : ils contiennent des mots de passe en clair.",
        'del /f /q "%WINDIR%\\Panther\\Unattend\\Unattend.xml"',
        'del /f /q "%WINDIR%\\Panther\\unattend.xml"',
        'del /f /q "%WINDIR%\\System32\\Sysprep\\unattend.xml"',
        'del "%~f0"',
    ]
    return "\r\n".join(lines) + "\r\n"
