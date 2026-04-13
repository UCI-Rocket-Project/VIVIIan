from connector_utils import StreamSpec, SendConnector, ReceiveConnector



'''
def main() -> None:
    with VIVIIan.backend as VIVII:
        id1 = "test"
        stream1 = VIVII.add_ReciveConnector(Streamspec, "DataIngressStream") 
        stream1.hash == some hash for the thing
        stream1.name == "DataIngressStream"
        #name is optional and when not used we fall back to the hash
        stream2  = VIVII.add_ReciveConnector(Streamspec, stream_X_id) 
        VIVII.add_ReciveConnector(Streamspec)

        # the user is responsible for writing it with pythusa compatible tasks 

        
        task1 = VIVII.add_task(some processing function, consumes from [stream1], task_name)
        task2 = VIVII.add_task(some processing function, consumes from [stream2, task1 ] )
        VIVII.add_SendConnector(Streamspec, consumes from , sendconnectortaskid)
        VIVII.add_SendConnector(Streamspec, consumes from task id one, task id two ....






'''

class Orchestrator: 


    def make_deviceinterface(): 

    def make_frontend(): 


    def make_backend(): 





    def make_connections(stream_spec: StreamSpec, sender_address: tuple[str, int], reciever_address: tuple[str, int]) -> None: 


    






