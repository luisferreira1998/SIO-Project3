import os
import sys
import base64
import json

from cryptography.x509 import Certificate
from cryptography.x509 import load_der_x509_certificate
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.hazmat.primitives.asymmetric import ec,rsa
from cryptography.hazmat.backends import default_backend

import cartao_cidadao
import assymetric_encryption
import certificates
import handshake_ec

USED_AUTH_IDS = set()
USER_DIRECTORY = "users/"
USERS = {}
NUMBER_OTPS = 100

def getID():
    return int.from_bytes(os.urandom(16), byteorder="big")


def getChallenge(method: str, cert: Certificate, user_name: str=None) -> dict:
    message = {
        "type" : "CHALLENGE",
        "challenge" : None,
        "certificate" : base64.b64encode(cert.public_bytes(Encoding.DER)).decode()
    }
    
    if method == "OTP":
        challenge = getChallengeOTP(user_name)
    elif method == "CERTIFICATE":
        challenge = getChallengeNonce()
    else:
        challenge = None
    
    message["challenge"] = challenge
    return message


def getChallengeOTP(user_name: str) -> dict:
    challenge = {
        "root" : "",
        "indice" : -1
    }
    
    if not user_name in USERS:
        raise Exception(f"User({user_name}) does exist.")

    user = USERS[user_name]
    root = user["root"]
    indice = user["indice"]

    challenge["root"] = base64.b64encode(root).decode()
    challenge["indice"] = indice

    return challenge

def getChallengeNonce():
    challenge = {
        "nonce" : base64.b64encode(os.urandom(32)).decode()
    }

    return challenge

def checkChallenge(message: dict):
    certificate_bytes = base64.b64decode( message["certificate"].encode())
    certificate = load_der_x509_certificate(certificate_bytes, default_backend())
    if not certificate.subject in certificates.Certificates:
        raise Exception("This is not the server certificate")

    loaded_certificate = certificates.Certificates[certificate.subject]
    c_fingerprint = certificates.fingerprint(certificate)
    lc_fingerprint = certificates.fingerprint(loaded_certificate)
    if c_fingerprint != lc_fingerprint:
        raise Exception("This is not not equal to the loaded server certificate")
 

def getChallengeResponse(method: str, message: dict, certificate: Certificate=None, dh_public_key: ec.EllipticCurvePublicKey=None, rsa_public_key: rsa.RSAPublicKey=None) -> dict:
    r_message = {
        "type" : "CHALLENGE_RESPONSE",
        "response" : {},
        "challenge" : getChallengeNonce()
    }

    challenge = message["challenge"]
    server_certificate_bytes = base64.b64decode( message["certificate"].encode() )
    server_certificate = load_der_x509_certificate(server_certificate_bytes, default_backend())
    server_public_key = server_certificate.public_key()

    if method == "OTP":
        response = getResponseOTP(challenge,dh_public_key,rsa_public_key)
    elif method == "CERTIFICATE":
        response = getResponseNonce(challenge, certificate)
    else:
        response = {}
    
    response_json = json.dumps(response).encode()
    cyphered_response = assymetric_encryption.encrypt(server_public_key, response_json)
    r_message["response"] = base64.b64encode(cyphered_response).decode()

    return r_message

def getResponseOTP(challenge: dict, dh_public_key: ec.EllipticCurvePublicKey=None, rsa_public_key: rsa.RSAPublicKey=None) -> dict:
    response = {
        "otp" : '',
        "dh_public_bytes" : '',
        "rsa_public_bytes" : ''
    }
    password = input(" - Password: ").encode()

    indice = challenge["indice"]
    root = base64.b64decode( challenge["root"].encode())
    otp = produce_otp(password, root, indice-1)

    dh_public_bytes = handshake_ec.getPeerPublicBytesFromKey(dh_public_key)
    rsa_public_bytes = assymetric_encryption.getPublicBytesFromKey(rsa_public_key)

    response["otp"] = base64.b64encode(otp).decode()
    response["dh_public_bytes"] = base64.b64encode(dh_public_bytes).decode()
    response["rsa_public_bytes"] = base64.b64encode(rsa_public_bytes).decode()

    return response


def getResponseNonce(challenge: dict, certificate: Certificate) -> dict:
    pass


def checkResponse(method: str, response: dict) -> dict:
    pass


def checkSuccess():
    pass

############################################################################################
#                                                                                          #
# OTP CODE                                                                                 #
#                                                                                          #
############################################################################################

def otp_matches(current_otp: bytes, otp_to_check: bytes) -> bool:
    digest_func = assymetric_encryption.buildDigestFunction()
    digest_func.update(otp_to_check)
    otp_to_check = digest_func.finalize()
    return current_otp == otp_to_check


def otp_status(user: dict) -> bool:
    return user["indice"] >= 0


def otp_check(user_name: str, otp_to_check: bytes) -> bool:
    user = USERS[user_name]

    if not otp_status(user):
        print(f" - User{user_name} has no more OTPS left.")
        return False
    
    current_otp = user["current_otp"]
    indice = user["indice"]

    if not otp_matches(current_otp, otp_to_check):
        return False
    
    indice -= 1
    current_otp = otp_to_check
    
    user["indice"] = indice
    user["current_otp"] = current_otp
    user_json = user_dict_to_json(user)
    write_user_file(user_name, user_json)

    return True
    

def produce_otp(password: bytes, root: bytes, indice: int):
    digest_func = assymetric_encryption.buildDigestFunction()
    digest_func.update(root)
    digest_func.update(password)
    current_otp = digest_func.finalize()

    for i in range(1,indice):
        digest_func = assymetric_encryption.buildDigestFunction()
        digest_func.update(current_otp)
        current_otp = digest_func.finalize()
    return current_otp

############################################################################################
#                                                                                          #
# USERS CODE                                                                               #
#                                                                                          #
############################################################################################

def load_users():
    dir_iter = os.scandir(USER_DIRECTORY)
    for entry in dir_iter:
        user = load_user(entry.path)
        user_key = entry.name.replace(".user","")
        USERS[user_key] = user


def load_user(file_name: str) -> dict:
    user = {
        "certificate" : b'',
        "current_otp" : b'',
        "indice" : 0,
        "root" : b'',
        "write_permission" : False
    }

    with open(file_name, "r") as user_file:
        user_json = json.loads(user_file.read())

        certificate_bytes = base64.b64decode(user_json["certificate"].encode())
        certificate = load_der_x509_certificate(certificate_bytes, default_backend())
        current_otp = base64.b64decode(user_json["current_otp"].encode())
        indice = user_json["indice"]
        root = base64.b64decode(user_json["root"].encode())
        write_permission = user_json["write_permission"]
    
    user["certificate"] = certificate
    user["current_otp"] = current_otp
    user["indice"] = indice
    user["root"] = root
    user["write_permission"] = write_permission
    return user


def user_dict_to_json(user: dict) -> dict:
    user_json = {
        "certificate" : "",
        "current_otp" : "",
        "indice" : user["indice"],
        "root" : "",
        "write_permission" : user["write_permission"]

    }
    
    certificate = user["certificate"]
    certificate_bytes = certificate.public_bytes(Encoding.DER)
    current_otp = user["current_otp"]
    root = user["root"]

    user_json["certificate"] = base64.b64encode(certificate_bytes).decode()
    user_json["current_otp"] = base64.b64encode(current_otp).decode()
    user_json["root"] = base64.b64encode(root).decode()

    return user_json


def write_user_file(user_name: str, user_json: dict):
    with open(f"{USER_DIRECTORY}{user_name}.user","w") as user_file:
        contents = json.dumps(user_json)
        user_file.write(contents)


def make_user(user_name: str, password: str, write_permission: bool=False):
    print(" - Making user...")
    user_json = {
        "certificate" : '',
        "current_otp" : '',
        "indice" : NUMBER_OTPS,
        "root" : '',
        "write_permission" : write_permission
    }

    root = os.urandom(16)
    print(f" - root: {root}")
    password = password.encode()
    
    current_otp = produce_otp(password, root, NUMBER_OTPS)
    
    _,certificate_bytes = cartao_cidadao.load_cert_auth_cc()
    certicate = base64.b64encode(certificate_bytes).decode()
    current_otp = base64.b64encode(current_otp).decode()

    user_json["certificate"] = certicate
    user_json["current_otp"] = current_otp
    user_json["root"] = base64.b64encode(root).decode()
    write_user_file(user_name,user_json)
    

############################################################################################
#                                                                                          #
# MAIN CODE                                                                                #
#                                                                                          #
############################################################################################

def get_args():
    return sys.argv[1:]

def register_user():
    username = input("Username to register: ")
    password = input("Password to register: ")
    write_permission = input("Has write permission: ") == "yes"
    make_user(username, password, write_permission)

############################################################################################
#                                                                                          #
# TEST CODE                                                                                #
#                                                                                          #
############################################################################################

def test():
    load_users()
    print(f" - Users: {USERS}")
    username = input(" - Username: ")
    password = input(f" - Password to check against user({username}): ")

    user = USERS[username]
    current_otp = user["current_otp"]
    root = user["root"]
    indice = user["indice"]
    otp_to_check = produce_otp(password.encode(), root, indice-1)
    is_equal = otp_matches(current_otp, otp_to_check)

    print(f" - root: {root}")
    print(f" - Are otps equal: {is_equal}")

    checks = otp_check(username, otp_to_check)

    print(f" - Do otps check: {checks}")
    print(f" - User after check: {user}")
    user = load_user(f"{USER_DIRECTORY}{username}.user")
    print(f" - File after check: {user}")

    server_cert = certificates.load_cert("server.cert.pem")
    chall_message = getChallenge("OTP", server_cert, username)
    client_priv_bytes,client_pub_bytes = assymetric_encryption.generateAssymetricKey()
    client_priv_key = assymetric_encryption.getPrivateKeyFromBytes(client_priv_bytes)
    client_pub_key = assymetric_encryption.getPublicKeyFromBytes(client_pub_bytes)
    dh_priv_key, dh_pub_key = handshake_ec.generateKeyPair()

    print(f" - Challenge Message: {chall_message}")
    checkChallenge(chall_message)
    chall_response = getChallengeResponse("OTP", chall_message, dh_public_key=dh_pub_key, rsa_public_key=client_pub_key)
    
    print(f" - Challenge Response: {chall_response}")

############################################################################################
#                                                                                          #
# MAIN CODE                                                                                #
#                                                                                          #
############################################################################################

if __name__ == "__main__":
    args = get_args()
    if len(args) != 1:
        print("Usage: python authentication.py <test|register>")
    elif args[0] == "test":
        test()
    elif args[0] == "register":
        register_user()
    else:
        print("Usage: python authentication.py <test|register>")
else:
    load_users()