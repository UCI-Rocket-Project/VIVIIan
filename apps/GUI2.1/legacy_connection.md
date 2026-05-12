Protocol
Board traffic in webservice/server.py uses TCP: each subsystem uses socket.socket(socket.AF_INET, socket.SOCK_STREAM) and connect(), then recv() / sendall() on the packed binary frames (not UDP).

The browser talks to the webservice over HTTP on TCP (Flask on port 8000). That is separate from the board sockets.

Where addresses are defined
webservice/constants.py does not define IP addresses or ports for the boards; it only has packet sizes, field names, and calibrations. Hosts and ports come from environment variables in server.py: ECU_IP, EXTR_ECU_IP, ECU_PORT, GSE_IP, GSE_PORT, LOAD_CELL_IP, LOAD_CELL_PORT.

Note: ECU and EXTR_ECU share the same port variable — extr_ecu_port = int(os.environ["ECU_PORT"]) — so they are only distinguished by IP.

Board addresses in your compose files
docker-compose-gui.yaml
Only the GUI (and postgres/adminer). No board IPs; the GUI does not define ECU/GSE/etc.

docker-compose-server.yaml

Variable	Value
ECU_IP
10.0.2.1
ECU_PORT
10001
GSE_IP
10.0.2.0
GSE_PORT
10001
LOAD_CELL_IP
10.0.255.2
LOAD_CELL_PORT
10001
This file does not set EXTR_ECU_IP, but server.py requires it (os.environ["EXTR_ECU_IP"]), so a container started only with this compose would need that variable added or supplied another way.

docker-compose-prod.yaml

Variable	Value
ECU_IP
10.0.2.1
EXTR_ECU_IP
10.0.2.67
ECU_PORT
10001 (used for both ECU and EXTR_ECU)
GSE_IP
10.0.2.3
GSE_PORT
10001
LOAD_CELL_IP
10.0.255.2
LOAD_CELL_PORT
10001
docker-compose-dev.yaml (with fake_rocket)

Variable	Value
ECU_IP
fake_rocket (Docker DNS hostname)
EXTR_ECU_IP
10.0.2.67
ECU_PORT
10004
GSE_IP
fake_rocket
GSE_PORT
10002
LOAD_CELL_IP
fake_rocket
LOAD_CELL_PORT
10069
webservice/.envtest (local test defaults, not used unless you load it) has the same style of 10.0.x.x addresses as in your earlier grep.

So: board link = TCP to (IP from env, port from env); concrete addresses depend on which compose (or env) you run; constants.py is not the source for those.