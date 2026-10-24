import xml.etree.ElementTree as ET

from app.unattend import build_setup_complete, build_unattend

NS = "{urn:schemas-microsoft-com:unattend}"
NASTY = "p&ss<w>o'r\"d & &amp; <![CDATA[ é€"


def build(**changes):
    args = dict(hostname="PC-01", domain="example.local", join_user="svc-join", join_password="join",
                admin_user="admin-local", admin_password="admin", timezone="Romance Standard Time",
                locale="fr-FR", ou="OU=ROOMA,OU=Ordinateurs,DC=example,DC=local")
    args.update(changes)
    return build_unattend(**args)


def texts(root, tag):
    return [e.text for e in root.iter(NS + tag)]


def test_xml_is_valid_whatever_the_passwords_contain():
    xml = build(join_password=NASTY, admin_password=NASTY + "2", admin_user="a&b<c>")
    assert xml.isascii()  # é et € sont écrits &#233; &#8364; : indépendant de l'encodage du fichier
    root = ET.fromstring(xml)
    assert texts(root, "Password")[0] == NASTY            # mot de passe de jonction au domaine
    assert texts(root, "Value") == [NASTY + "2"]          # mot de passe du compte local
    assert texts(root, "Name") == ["a&b<c>"]


def test_content_of_the_answer_file():
    root = ET.fromstring(build())
    assert texts(root, "ComputerName") == ["PC-01"] and texts(root, "TimeZone") == ["Romance Standard Time"]
    assert texts(root, "JoinDomain") == ["example.local"] and texts(root, "Domain") == ["example.local"]
    assert texts(root, "Username") == ["svc-join"]
    assert texts(root, "MachineObjectOU") == ["OU=ROOMA,OU=Ordinateurs,DC=example,DC=local"]
    assert texts(root, "Group") == ["Administrators"] and texts(root, "PlainText") == ["true"]
    assert set(texts(root, "UserLocale") + texts(root, "InputLocale") + texts(root, "UILanguage")
               + texts(root, "SystemLocale")) == {"fr-FR"}
    assert [s.get("pass") for s in root.iter(NS + "settings")] == ["specialize", "oobeSystem"]
    assert texts(root, "ProtectYourPC") == ["3"] and texts(root, "HideEULAPage") == ["true"]


def test_optional_ou_is_left_out():
    for ou in (None, ""):
        xml = build(ou=ou)
        assert "MachineObjectOU" not in xml
        ET.fromstring(xml)


def test_setup_complete_deletes_the_answer_files_then_itself():
    cmd = build_setup_complete()
    assert "\r\n" in cmd and "\n" not in cmd.replace("\r\n", "")  # fins de ligne Windows partout
    lines = cmd.split("\r\n")
    assert 'del /f /q "%WINDIR%\\Panther\\Unattend\\Unattend.xml"' in lines
    assert 'del /f /q "%WINDIR%\\Panther\\unattend.xml"' in lines
    assert 'del /f /q "%WINDIR%\\System32\\Sysprep\\unattend.xml"' in lines
    assert lines[-2] == 'del "%~f0"'  # dernière ligne utile : le script s'efface lui-même
