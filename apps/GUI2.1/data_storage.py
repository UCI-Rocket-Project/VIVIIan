import time
import pyarrow as pa
import pyarrow.parquet as pq

def write_from_stream(stream, path, column_names, column_types, file_size: int = 1024 * 1024 * 10):
    """
    Reads data from a stream and writes it to rolling Parquet files.
    A new file is created in the specified path once the current file exceeds `file_size`.
    """
    
    # 1. Ensure the destination folder exists (assuming 'path' is a pathlib.Path object)
    path.mkdir(parents=True, exist_ok=True)
    
    # 2. Define the blueprint for the Parquet files (Fixed typo here)
    schema = pa.schema([(name, column_types[i]) for i, name in enumerate(column_names)])
    
    # Outer loop: Handles creating a brand new file
    while True: 
        file_path = path / f"part-{time.time_ns()}.parquet"
        bytes_written = 0
        
        # Open the writer for the new file
        with pq.ParquetWriter(file_path, schema) as writer:
            
            # Inner loop: Keeps writing to the CURRENT file until it gets too big
            while bytes_written < file_size:
                
                frame = stream.read() # Expected to return a list/tuple of numpy arrays or 2D array
                
                if frame is None:
                    # Note: If 'None' means the stream is permanently finished, change 
                    # this to 'return' so the function ends. 
                    # If it just means "no data right now", we wait a tiny bit so 
                    # the CPU doesn't spin at 100% usage doing nothing.
                    time.sleep(0.01) 
                    continue
                
                # Convert the NumPy data into a PyArrow Table
                columns = [frame[:, i] for i in range(frame.shape[1])]
                table = pa.Table.from_arrays(columns, names=column_names)
                
                # Append the chunk to the open file
                writer.write_table(table)
                
                # Add the size of this PyArrow table to our tracker
                bytes_written += table.nbytes
                
        # Once bytes_written >= file_size, the 'while' loop breaks.
        # The 'with' block ends, automatically closing and finalizing the Parquet file.
        # The outer loop restarts, generating a new timestamped file_path!