import os
from dotenv import load_dotenv
from langchain_community.document_loaders import DirectoryLoader, TextLoader, UnstructuredMarkdownLoader, PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma


# Load các biến môi trường từ file .env
load_dotenv()


EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
PERSIST_DIRECTORY = os.getenv("PERSIST_DIRECTORY", "./chroma_db_local")
KNOWLEDGE_BASE_PATH = os.getenv("KNOWLEDGE_BASE_PATH", "./specs")
OUTPUT_RTL_PATH = os.getenv("OUTPUT_RTL_PATH", "./output_rtl")
OUTPUT_PASS_PATH = os.getenv("OUTPUT_PASS_PATH", "./output_pass")


embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_NAME)


def build_or_update_vector_db(is_auto_update=False, source_paths=None):
    """
    is_auto_update = False: Chạy lúc khởi động Agent (Có hỏi y/n)
    is_auto_update = True: Tự động chạy sau khi 1 module PASS (Âm thầm nạp code mới, không hỏi)
    source_paths: Khi auto update, chỉ nạp các file vừa PASS để tránh đưa artifact lỗi/cũ vào RAG.
    """
    db_exists = os.path.exists(PERSIST_DIRECTORY) and len(os.listdir(PERSIST_DIRECTORY)) > 0
    documents = []


    # =========================================================
    # TRƯỜNG HỢP 1: KHỞI ĐỘNG BAN ĐẦU (CÓ HỎI Y/N)
    # =========================================================
    if not is_auto_update:
        print("\n" + "="*60)
        print("🧠 HỆ THỐNG TRÍ TUỆ NHÂN TẠO (RAG ENGINE)")
        print("="*60)


        if db_exists:
            while True:
                ans = input(f"[?] Bạn có tài liệu Spec/Rules mới, hoặc vừa chỉnh sửa tay code RTL không? (y/n): ").strip().lower()
                if ans in ['y', 'yes', 'n', 'no']:
                    break
                print("[!] Vui lòng nhập 'y' hoặc 'n'.")
               
            if ans in ['n', 'no']:
                print("-> [RAG] Đã nạp Database cũ từ ổ cứng. Khởi động Agent ngay...\n")
                return


        print("\n-> [RAG] Đang đọc và băm nhỏ tài liệu (PDF, MD, TXT, Code SV)...")
       
        # Nạp PDF, MD, TXT từ Knowledge Base
        if os.path.exists(KNOWLEDGE_BASE_PATH):
            try:
                pdf_loader = DirectoryLoader(KNOWLEDGE_BASE_PATH, glob="**/*.pdf", loader_cls=PyPDFLoader)
                documents.extend(pdf_loader.load())
            except: pass
           
            try:
                md_loader = DirectoryLoader(KNOWLEDGE_BASE_PATH, glob="**/*.md", loader_cls=UnstructuredMarkdownLoader)
                documents.extend(md_loader.load())
            except: pass
           
            try:
                txt_loader = DirectoryLoader(KNOWLEDGE_BASE_PATH, glob="**/*.txt", loader_cls=TextLoader)
                documents.extend(txt_loader.load())
            except: pass


        # Nạp artifact đã PASS trước đó
        if os.path.exists(OUTPUT_PASS_PATH):
            try:
                sv_loader = DirectoryLoader(OUTPUT_PASS_PATH, glob="**/*.sv", loader_cls=TextLoader)
                documents.extend(sv_loader.load())
            except: pass


    # =========================================================
    # TRƯỜNG HỢP 2: TỰ ĐỘNG HỌC CODE MỚI SAU KHI PASS (KHÔNG HỎI)
    # =========================================================
    else:
        print("-> [RAG] Âm thầm học module vừa PASS vào Database...")
        if source_paths:
            for source_path in source_paths:
                if not source_path or not os.path.exists(source_path):
                    continue
                try:
                    documents.extend(TextLoader(source_path).load())
                except Exception as e:
                    print(f"[!] Lỗi nạp file PASS '{source_path}': {e}")
        elif os.path.exists(OUTPUT_RTL_PATH):
            try:
                sv_loader = DirectoryLoader(OUTPUT_RTL_PATH, glob="**/*.sv", loader_cls=TextLoader)
                documents.extend(sv_loader.load())
            except Exception as e:
                print(f"[!] Lỗi nạp code RTL tự động: {e}")


    # =========================================================
    # XỬ LÝ CHUNKING VÀ LƯU VÀO DB
    # =========================================================
    if not documents:
        return


    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=200)
    chunks = text_splitter.split_documents(documents)


    vectorstore = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=PERSIST_DIRECTORY
    )
   
    if not is_auto_update:
        print(f"-> [RAG] THÀNH CÔNG! Đã lưu {len(chunks)} khối kiến thức.\n")


def retrieve_context(query, k=4):
    """Hàm tra cứu thông tin siêu tốc từ Vector DB"""
    if not os.path.exists(PERSIST_DIRECTORY):
        return "No reference context available."
       
    vectorstore = Chroma(persist_directory=PERSIST_DIRECTORY, embedding_function=embeddings)
    retriever = vectorstore.as_retriever(search_kwargs={"k": k})
    docs = retriever.invoke(query)
   
    context = "\n\n".join([f"--- [NGUỒN: {doc.metadata.get('source', 'Unknown')}] ---\n{doc.page_content}" for doc in docs])
    return context
