import os
import chromadb

from markdown_utils import parse_markdown_blocks


def build_faq_collection(client, collection_name="vetic_faq"):
    """Create a separate FAQ vector collection from the markdown FAQ file."""
    faq_path = os.path.join("knowledge", "faq_and_flow.txt")
    if not os.path.isfile(faq_path):
        print(f"FAQ file not found at {faq_path}")
        return None

    with open(faq_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Parse markdown into (header, block) tuples
    faq_blocks = parse_markdown_blocks(content)

    docs, metas, ids = [], [], []
    for i, (header, block) in enumerate(faq_blocks):
        block = block.strip()
        if not block:
            continue
        docs.append(block)
        metas.append({"source": "faq_and_flow.txt", "section": header or f"FAQ_{i}"})
        ids.append(f"faq_{i}")

    # Recreate the collection to avoid duplicates
    try:
        client.delete_collection(collection_name)
    except Exception:
        pass
    collection = client.create_collection(collection_name)
    if docs:
        collection.add(documents=docs, metadatas=metas, ids=ids)
        print(f"[OK] Built FAQ collection with {len(docs)} entries")
    else:
        print("[WARN] No FAQ entries indexed")
    return collection

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
    faq_collection = build_faq_collection(client)
    
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
            
        # Parse markdown into (header, block) tuples for structure-aware chunking
        blocks = parse_markdown_blocks(content)

        for header, block in blocks:
            # Determine a suitable header for metadata
            current_header = header or filename

            # Skip excluded sections
            if "13. Complete Output Mapping Table" in current_header:
                continue

            block = block.strip()
            if not block:
                continue

            # If block is too large or contains a table, further split by lines
            if len(block) > MAX_CHUNK_SIZE or "|" in block:
                lines = block.split("\n")
                table_header = ""
                if len(lines) >= 2 and "|" in lines[0] and "---" in lines[1]:
                    table_header = lines[0] + "\n" + lines[1] + "\n"
                    lines = lines[2:]

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
                context_chunk = f"[{current_header}]\n{block}"
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
