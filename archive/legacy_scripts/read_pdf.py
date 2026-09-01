import PyPDF2

pdf_path = r'C:\Users\hp\Desktop\claude_WO_API\VeticxConvoZen (1).pdf'

with open(pdf_path, 'rb') as f:
    reader = PyPDF2.PdfReader(f)
    print(f'Total Pages: {len(reader.pages)}')
    for i, page in enumerate(reader.pages):
        print(f'\n--- PAGE {i+1} ---')
        print(page.extract_text())
