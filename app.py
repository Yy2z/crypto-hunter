import streamlit as st
import os
import json
import pandas as pd
import re
from dotenv import load_dotenv
from openai import OpenAI
from tavily import TavilyClient

# ============================================================================
# 1. 基础配置
# ============================================================================
st.set_page_config(
    page_title="Crypto BD Hunter: Architect Edition",
    page_icon="⚔️",
    layout="wide"
)

# 自定义 CSS 优化表格显示
st.markdown("""
<style>
    .stDataFrame { border: 1px solid #f0f0f0; border-radius: 5px; }
    div[data-testid="stStatusWidget"] { font-weight: bold; }
</style>
""", unsafe_allow_html=True)

load_dotenv()

with st.sidebar:
    st.header("⚙️ 核心引擎")
    deepseek_key = st.text_input("DeepSeek Key", value=os.getenv("DEEPSEEK_API_KEY", ""), type="password")
    tavily_key = st.text_input("Tavily Key", value=os.getenv("TAVILY_API_KEY", ""), type="password")
    st.info("💡 Tip: 即使填反了推特和官网，系统现在也能自动识别。")

if not deepseek_key or not tavily_key:
    st.warning("⚠️ 请先配置 API Keys")
    st.stop()

# 初始化客户端
try:
    llm = OpenAI(api_key=deepseek_key, base_url="https://api.deepseek.com")
    tavily = TavilyClient(api_key=tavily_key)
except Exception as e:
    st.error(f"Client Init Error: {e}")
    st.stop()

# ============================================================================
# 2. 智能输入处理 (Smart Input Processor)
# ============================================================================

def auto_detect_fingerprints(input_website, input_twitter):
    """
    不管用户填在哪个框，自动识别谁是官网，谁是推特。
    """
    fingerprints = {
        "twitter_handle": None,
        "domain": None
    }
    
    # 合并输入进行分析
    inputs = [input_website, input_twitter]
    
    for item in inputs:
        if not item: continue
        item = item.strip().lower()
        
        # 识别推特
        if "x.com" in item or "twitter.com" in item:
            # 提取 handle: x.com/Weex_Official -> Weex_Official
            try:
                # 移除末尾斜杠和参数
                clean_url = item.split("?")[0].rstrip("/")
                handle = clean_url.split("/")[-1]
                if handle:
                    fingerprints["twitter_handle"] = handle
            except:
                pass
        
        # 识别官网 (排除推特、领英)
        elif "." in item and "linkedin" not in item:
            # 提取域名: https://www.weex.com/ -> weex.com
            try:
                clean_url = item.replace("https://", "").replace("http://", "").split("/")[0]
                # 移除 www.
                if clean_url.startswith("www."):
                    clean_url = clean_url[4:]
                fingerprints["domain"] = clean_url
            except:
                pass
                
    return fingerprints

# ============================================================================
# 3. 瀑布流搜索策略 (Waterfall Search Strategy)
# ============================================================================

def generate_waterfall_queries(project_name, category, fps):
    queries = []
    
    # 基础过滤：强制 Crypto 上下文 + 排除餐厅/实体店
    base_context = "crypto OR blockchain OR web3 OR exchange OR token"
    negative_filter = "-restaurant -steakhouse -chef -menu -food -dining -recipe"
    
    # 角色关键词
    if category == "VC":
        roles = "Partner OR Investor"
    else:
        roles = "Founder OR CEO OR CMO OR \"Head of Listing\" OR \"Head of BD\""

    # --- Level 1: 精准狙击 (如果指纹存在) ---
    # 逻辑：很多 Crypto 人的领英简介会写 "Founder @Weex_Official"
    if fps["twitter_handle"]:
        queries.append(f"site:linkedin.com \"@{fps['twitter_handle']}\"")
        queries.append(f"site:linkedin.com \"{fps['twitter_handle']}\"")
    
    if fps["domain"]:
        queries.append(f"site:linkedin.com \"{fps['domain']}\" {roles}")

    # --- Level 2: 强关联搜索 (项目名 + 行业词) ---
    # 逻辑：必须同时出现 Project Name 和 Crypto 词汇，否则不要
    queries.append(f"site:linkedin.com/in/ \"{project_name}\" {base_context} {roles} {negative_filter}")
    queries.append(f"site:linkedin.com/company/ \"{project_name}\" {base_context}")

    # --- Level 3: 兜底搜索 (如果找不到领英，找其他来源) ---
    queries.append(f"\"{project_name}\" {base_context} team listing contact {negative_filter}")
    
    return queries

def execute_search_layer(queries, max_results=5):
    all_results = []
    seen_urls = set()
    
    with st.status("🦅 正在执行瀑布流搜索...", expanded=True) as status:
        for q in queries:
            st.write(f"📡 扫描: {q}")
            try:
                response = tavily.search(
                    query=q,
                    search_depth="advanced",
                    max_results=max_results,
                    include_answer=False
                )
                
                for r in response.get('results', []):
                    # 再次在代码层做一次过滤，防止 API 漏网之鱼
                    content_lower = (r['title'] + r['content']).lower()
                    if "steak" in content_lower or "restaurant" in content_lower or "menu" in content_lower:
                        continue # 丢弃餐厅结果
                        
                    if r['url'] not in seen_urls:
                        all_results.append(r)
                        seen_urls.add(r['url'])
                        
            except Exception as e:
                print(f"Query failed: {q} - {e}")
        
        status.update(label=f"✅ 捕获 {len(all_results)} 条有效情报，开始 AI 分析...", state="running", expanded=False)
    
    return all_results

# ============================================================================
# 4. 修复版 AI 分析 (Scope Fix + URL Fix)
# ============================================================================

def normalize_url(url):
    """修复 URL 跳转问题"""
    if not url or not isinstance(url, str): return None
    url = url.strip()
    if len(url) < 5 or "none" in url.lower() or "n/a" in url.lower(): return None
    
    # 补全协议
    if not url.startswith("http"):
        return "https://" + url
    return url

def analyze_with_deepseek(project_name, search_results, fps):
    # 构建 URL 仓库
    url_registry = []
    content_feed = []
    
    for idx, r in enumerate(search_results):
        source_id = f"S{idx+1}"
        # 只要是领英或推特，都加粗放入注册表
        if "linkedin.com" in r['url'] or "x.com" in r['url']:
            url_registry.append(f"[{source_id}] {r['url']} (Title: {r['title']})")
        
        content_feed.append(f"Source [{source_id}]\nURL: {r['url']}\nContent: {r['content'][:800]}\n---\n")
    
    registry_text = "\n".join(url_registry)
    feed_text = "\n".join(content_feed)
    
    prompt = f"""
    Target Project: "{project_name}"
    Context: Crypto/Web3 Industry.
    Detected Fingerprints: {fps}
    
    TASK: Extract verified Team Members and Official Contacts.
    
    CRITICAL RULES:
    1. **NO STEAKHOUSES**: If the content is about food/restaurants (e.g. "Fogo de Chao"), IGNORE IT.
    2. **LINK MATCHING**: You MUST try to find a URL from the "URL REGISTRY" for every person.
       - If you see "Stephen Chen" in Source S1, and S1's URL is a LinkedIn profile, USE IT.
       - Do NOT output "LinkedIn Profile" as text. Output the actual URL or "N/A".
    3. **RECALL**: If you find a person but no link, list them anyway.
    
    URL REGISTRY (Pick links from here):
    {registry_text}
    
    SEARCH CONTENT:
    {feed_text}
    
    OUTPUT JSON:
    {{
        "team": [ {{ "name": "...", "role": "...", "linkedin": "URL/N/A", "twitter": "URL/N/A" }} ],
        "contacts": [ {{ "type": "...", "value": "...", "note": "..." }} ]
    }}
    """
    
    try:
        response = llm.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "You are a JSON extractor. Output valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            response_format={ "type": "json_object" }
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        st.error(f"AI Analysis Error: {e}")
        return None

# ============================================================================
# 5. 主界面
# ============================================================================

st.title("⚔️ Crypto BD Hunter: Architect Edition")
st.markdown("智能输入纠错 | 瀑布流搜索 | 实体店过滤")

# --- 输入区 ---
with st.container():
    col1, col2 = st.columns([3, 1])
    with col1:
        target_project = st.text_input("Project Name", placeholder="e.g. Weex, Monad, Fogo")
    with col2:
        category = st.selectbox("Category", ["Project", "VC", "Exchange"])

    with st.expander("🕵️‍♂️ 辅助线索 (乱填也没事，我会自动识别)", expanded=True):
        col3, col4 = st.columns(2)
        with col3:
            # 即使这里填了官网，下面代码也能识别
            input_twitter = st.text_input("Official Twitter (or Website)", placeholder="Paste any link here")
        with col4:
            # 即使这里填了推特，下面代码也能识别
            input_website = st.text_input("Official Website (or Twitter)", placeholder="Paste any link here")

# --- 逻辑核心 ---
if st.button("🚀 启动深潜模式", type="primary"):
    if not target_project:
        st.toast("⚠️ 请输入项目名称")
        st.stop()
        
    # 1. 智能识别指纹 (修复 Input Error)
    fps = auto_detect_fingerprints(input_website, input_twitter)
    
    # 显示识别结果给用户看
    if fps['twitter_handle'] or fps['domain']:
        st.success(f"🧬 成功提取指纹: Handle=[@{fps['twitter_handle']}] | Domain=[{fps['domain']}]")
    else:
        st.info("⚠️ 未检测到有效指纹，将使用通用搜索模式。")
    
    # 2. 生成并执行搜索
    queries = generate_waterfall_queries(target_project, category, fps)
    raw_data = execute_search_layer(queries)
    
    # 3. AI 分析 (修复 Scope Error)
    ai_result = None  # 初始化变量
    
    if raw_data:
        with st.spinner("🧠 正在清洗数据并排除无关实体..."):
            ai_result = analyze_with_deepseek(target_project, raw_data, fps)
    else:
        st.error("❌ 全网未找到相关 Crypto 信息。可能原因：项目名拼写错误或该项目没有任何公开 Web3 足迹。")
    
    # 4. 结果展示
    if ai_result:
        # --- Team ---
        st.subheader("👥 核心团队 (Verified)")
        if ai_result.get("team"):
            df_team = pd.DataFrame(ai_result["team"])
            # 修复 URL
            for col in ["linkedin", "twitter"]:
                if col in df_team.columns:
                    df_team[col] = df_team[col].apply(normalize_url)
            
            st.dataframe(
                df_team,
                column_config={
                    "linkedin": st.column_config.LinkColumn("LinkedIn", display_text="View Profile"),
                    "twitter": st.column_config.LinkColumn("Twitter", display_text="Open X"),
                },
                use_container_width=True,
                hide_index=True
            )
        else:
            st.warning("未找到个人档案。")

        # --- Contacts ---
        st.subheader("📬 官方渠道")
        if ai_result.get("contacts"):
            df_contacts = pd.DataFrame(ai_result["contacts"])
            if "value" in df_contacts.columns:
                df_contacts["value"] = df_contacts["value"].apply(normalize_url)
                
            st.dataframe(
                df_contacts,
                column_config={
                    "value": st.column_config.LinkColumn("Link", display_text="Open Link")
                },
                use_container_width=True,
                hide_index=True
            )
        
        # --- Export ---
        st.divider()
        try:
            # 安全导出逻辑
            export_data = []
            for t in ai_result.get("team", []):
                export_data.append({"Type": "Person", "Name": t.get('name'), "Role": t.get('role'), "Link": t.get('linkedin')})
            for c in ai_result.get("contacts", []):
                export_data.append({"Type": "Channel", "Name": c.get('type'), "Desc": c.get('note'), "Link": c.get('value')})
            
            if export_data:
                csv = pd.DataFrame(export_data).to_csv(index=False).encode('utf-8')
                st.download_button("📥 导出结果", data=csv, file_name=f"{target_project}_Hunter_Report.csv")
        except Exception as e:
            st.error(f"导出准备失败: {e}")