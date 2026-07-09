import os
import chromadb

def main():
    knowledge_dir = "knowledge"
    db_path = "knowledge_db"
    
    if not os.path.isdir(knowledge_dir):
        print(f"Directory {knowledge_dir} not found.")
        return

    print(f"Initializing ChromaDB at {db_path}...")
    client = chromadb.PersistentClient(path=db_path)
    
    # We use the default embedding function (all-MiniLM-L6-v2)
    # Recreate the collection to avoid duplicates if run multiple times
    try:
        client.delete_collection("vetic_knowledge")
    except Exception:
        pass
        
    collection = client.create_collection("vetic_knowledge")
    
    documents = []
    metadatas = []
    ids = []
    
    doc_id_counter = 0
    MAX_CHUNK_SIZE = 800  # Characters (safe for MiniLM 256 token limit)

    for filename in os.listdir(knowledge_dir):
        if not filename.endswith(".txt"):
            continue
            
        filepath = os.path.join(knowledge_dir, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
            
        # Split by double newlines first (logical paragraphs/sections)
        paragraphs = content.split("\n\n")
        current_header = filename
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
                
            if para.startswith("#"):
                current_header = para.split("\n")[0].strip("# ")
            
            # Exclude specific sections from chunking
            if "13. Complete Output Mapping Table" in current_header:
                continue
            
            # If the paragraph is a table or very long, split it further by lines
            if len(para) > MAX_CHUNK_SIZE or "|" in para:
                lines = para.split("\n")
                
                # Check if it's a markdown table and extract the table header
                table_header = ""
                if len(lines) >= 2 and "|" in lines[0] and "---" in lines[1]:
                    table_header = lines[0] + "\n" + lines[1] + "\n"
                    lines = lines[2:] # skip header rows
                
                current_chunk = ""
                for line in lines:
                    if len(current_chunk) + len(line) > MAX_CHUNK_SIZE and current_chunk:
                        context_chunk = f"[{current_header}]\n{table_header}{current_chunk.strip()}"
                        documents.append(context_chunk)
                        metadatas.append({"source": filename, "section": current_header})
                        ids.append(f"doc_{doc_id_counter}")
                        doc_id_counter += 1
                        current_chunk = ""
                        
                    current_chunk += line + "\n"
                    
                if current_chunk.strip():
                    context_chunk = f"[{current_header}]\n{table_header}{current_chunk.strip()}"
                    documents.append(context_chunk)
                    metadatas.append({"source": filename, "section": current_header})
                    ids.append(f"doc_{doc_id_counter}")
                    doc_id_counter += 1
            else:
                context_chunk = f"[{current_header}]\n{para}"
                documents.append(context_chunk)
                metadatas.append({"source": filename, "section": current_header})
                ids.append(f"doc_{doc_id_counter}")
                doc_id_counter += 1
            
    if documents:
        print(f"Adding {len(documents)} chunks to the vector database...")
        # Add in batches to be safe, though Chroma handles small arrays fine
        collection.add(
            documents=documents,
            metadatas=metadatas,
            ids=ids
        )
        print("Successfully created the vector database!")
    else:
        print("No documents found to index.")

if __name__ == "__main__":
    main()
