def parse_markdown_blocks(content):
    lines = content.split('\n')
    blocks = []
    current_header = ""
    current_block = []

    for line in lines:
        if line.startswith('#'):
            if current_block:
                blocks.append((current_header, '\n'.join(current_block)))
                current_block = []
            current_header = line.strip('#').strip()
        else:
            current_block.append(line)
            
    if current_block:
        blocks.append((current_header, '\n'.join(current_block)))
        
    return blocks
