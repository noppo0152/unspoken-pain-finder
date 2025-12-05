import streamlit as st
import os
from google import genai
import sqlite3
import hashlib
import pandas as pd
from typing import Optional 

# --- データベース初期化 ---
conn = sqlite3.connect('user_data.db')
c = conn.cursor()

# 1. ユーザーテーブルを最初に作成する (存在しなければ)
#    → これで、後続のALTER TABLE操作（plan列の追加）が安全になる
c.execute('''
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password TEXT,
        plan TEXT DEFAULT "free" 
    )
''')
conn.commit()

# 2. テーブル作成後に、plan列を追加する関数を定義
def add_plan_column():
    """usersテーブルにplan列を追加（既に存在する場合は何もしない）"""
    try:
        # 新規ユーザーはデフォルトで"free"プラン
        c.execute('ALTER TABLE users ADD COLUMN plan TEXT DEFAULT "free"')
        conn.commit()
    except sqlite3.OperationalError as e:
        # すでにplan列が存在する場合は無視 (エラーを握りつぶす)
        if "duplicate column name" not in str(e):
            # plan列の追加に失敗した場合のみエラーを再発生させる
            raise e

# 3. plan列の存在を保証（テーブル作成後なので安全）
add_plan_column() 

# アイデア保存用のテーブルを作成
c.execute('''
    CREATE TABLE IF NOT EXISTS ideas (
        id INTEGER PRIMARY KEY,
        username TEXT,
        input TEXT,
        output TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(username) REFERENCES users(username)
    )
''')
conn.commit()


# --- 設定 ---
# デプロイ環境のStreamlit SecretsからAPIキーを読み込みます
api_key = os.getenv("GEMINI_API_KEY") 

if not api_key:
    # 環境変数が未設定の場合、エラーを出して停止
    st.error("エラー: GEMINI_API_KEYが設定されていません。デプロイの際はStreamlit Secretsを設定してください。")
    st.stop()

client = genai.Client(api_key=api_key)
model_name = 'gemini-2.5-flash'


# --- 認証・データ操作関数 ---
def make_hashes(password: str) -> str:
    """パスワードをハッシュ化"""
    return hashlib.sha256(password.encode()).hexdigest()

def check_hashes(password: str, hashed_text: str) -> bool:
    """ハッシュ化されたパスワードの検証"""
    return make_hashes(password) == hashed_text

def add_user(username: str, password: str):
    """ユーザーをデータベースに追加 (デフォルトはfreeプラン)"""
    hashed_password = make_hashes(password)
    c.execute('INSERT INTO users (username, password, plan) VALUES (?,?,?)', (username, hashed_password, 'free'))
    conn.commit()

def login_user(username: str, password: str) -> Optional[str]:
    """ログイン検証"""
    c.execute('SELECT * FROM users WHERE username = ?', (username,))
    user_record = c.fetchone()
    if user_record and check_hashes(password, user_record[1]):
        return user_record[0] # ユーザー名を返す
    return None

def save_idea(username: str, user_input: str, ai_output: str):
    """ユーザーの入力とAIの出力をデータベースに保存"""
    c.execute(
        'INSERT INTO ideas (username, input, output) VALUES (?, ?, ?)', 
        (username, user_input, ai_output)
    )
    conn.commit()

def get_user_ideas(username: str) -> list:
    """ユーザーが保存したアイデアをすべて取得"""
    c.execute(
        'SELECT id, timestamp, input, output FROM ideas WHERE username = ? ORDER BY timestamp DESC', 
        (username,)
    )
    return c.fetchall()

def count_user_ideas(username: str) -> int:
    """ユーザーが保存したアイデアの総数を取得"""
    c.execute('SELECT COUNT(*) FROM ideas WHERE username = ?', (username,))
    return c.fetchone()[0]

def get_user_plan(username: str) -> str:
    """ユーザーのプラン（free or pro）を取得"""
    c.execute('SELECT plan FROM users WHERE username = ?', (username,))
    result = c.fetchone()
    # デフォルト値は "free"
    return result[0] if result else 'free'

def upgrade_user_plan(username: str):
    """ユーザーのプランを 'pro' に更新する (決済完了後の処理を想定)"""
    c.execute('UPDATE users SET plan = "pro" WHERE username = ?', (username,))
    conn.commit()


# --- Streamlit UI ---
st.sidebar.title("アカウント認証")

if 'logged_in_user' not in st.session_state:
    st.session_state['logged_in_user'] = None

if st.session_state['logged_in_user']:
    # ログイン後のメイン画面
    current_user = st.session_state['logged_in_user']
    user_plan = get_user_plan(current_user)
    
    st.sidebar.success(f"ようこそ、{current_user}さん！ (プラン: **{user_plan.upper()}**)")
    
    st.title("💡 Unspoken-Pain Finder (Pro版へ進化中)")
    st.caption("あなたの副業アイデアをAIで深く掘り下げ、履歴として保存・管理しましょう。")
    
    tab1, tab2, tab3 = st.tabs(["アイデア生成", "マイアイデア履歴", "アカウント設定 (開発中)"])

    # --- タブ 1: アイデア生成 ---
    with tab1:
        saved_count = count_user_ideas(current_user)
        MAX_FREE_COUNT = 5 # 無料ユーザーの最大保存数
        can_save = True

        # 制限アラートの表示ロジック
        if user_plan == 'free':
            if saved_count >= MAX_FREE_COUNT:
                st.warning(f"💡 無料プランの上限（{MAX_FREE_COUNT}件）に達しました。深掘り質問は可能ですが、保存はされません。Pro版をご利用ください！")
                can_save = False
            else:
                 st.info(f"💾 現在 {saved_count}/{MAX_FREE_COUNT} 件のアイデアを保存中です。（無料プラン）")
        
        # --------------------------------------------------------------------------------------
        
        user_input = st.text_area(
            "あなたのアイデアの種を入力してください:",
            height=150,
            key="input_tab1" 
        )
        
        if st.button("深掘り質問を生成", key="generate_button_tab1"):
            if user_input:
                with st.spinner("Geminiが思考中..."):
                    prompt = f"""
                    あなたは最高のブレインストーミングアシスタントです。
                    ユーザーが入力した「アイデアの種」を、具体的で実用的な3つの深掘り質問に変換してください。
                    質問は、ユーザーが自分の問題点を明確にしたり、解決策のヒントを見つけるのに役立つものでなければなりません。
                    ユーザーの入力: "{user_input}"
                    期待する出力形式: 1. 〇〇 2. 〇〇 3. 〇〇
                    """
                    try:
                        response = client.models.generate_content(
                            model=model_name,
                            contents=prompt
                        )
                        st.subheader("🤖 Geminiからの深掘り質問")
                        st.markdown(response.text)
                        
                        if can_save:
                            # アイデア保存機能を有効化
                            save_idea(current_user, user_input, response.text)
                            st.success("アイデアと質問を保存しました！") 
                            st.rerun() # 保存後のカウンター更新のため再実行
                        else:
                            st.info("保存数の上限を超えたため、今回は保存されませんでした。（Pro版へのアップグレードを推奨します）")


                    except Exception as e:
                        st.error(f"エラーが発生しました: {e}")
            else:
                st.warning("何か入力してください。")

    # --- タブ 2: アイデア履歴 ---
    with tab2:
        st.subheader(f"{current_user}さんのアイデア履歴")
        ideas = get_user_ideas(current_user)
        
        if ideas:
            # pandas DataFrameに変換して表示 (見やすくするため)
            df = pd.DataFrame(
                ideas, 
                columns=['ID', '日時', '入力内容', '深掘り質問']
            )
            # 日時と入力内容のみをDataFrameとして表示
            st.dataframe(df[['日時', '入力内容']], use_container_width=True, hide_index=True)
            
            # 詳細表示
            st.markdown("---")
            for _, row in df.iterrows():
                with st.expander(f"**[{row['日時']}]** {row['入力内容'][:50]}..."): # クリックして開く形式
                    st.markdown(f"**入力内容:** {row['入力内容']}")
                    st.markdown("**AIの深掘り質問:**")
                    st.markdown(row['深掘り質問'])
        else:
            st.info("まだ保存されたアイデアはありません。")

    # --- タブ 3: アカウント設定 ---
    with tab3:
        st.subheader("Pro版アップグレード")
        st.write(f"現在のプラン: **{user_plan.upper()}**")
        
        if user_plan == 'free':
            st.markdown("""
                **Pro版の特典:**
                * アイデア保存数が**無制限**になります。
                * より高度な分析を行う **Gemini 2.5 Pro** モデルが利用可能になります。（今後実装）
            """)
            
            # アップグレードボタンが押されたときの処理
            if st.button("Pro版へアップグレードする (模擬決済)"):
                upgrade_user_plan(current_user)
                st.success("🎉 Pro版にアップグレードされました！再起動しています...")
                st.rerun()
                
        else:
            st.success("✅ あなたは現在Pro版ユーザーです！すべての機能が無制限でご利用いただけます。")

        
    st.sidebar.button("ログアウト", on_click=lambda: st.session_state.pop('logged_in_user'))
    
else:
    # ログアウト状態の表示
    menu = st.sidebar.selectbox("メニュー", ["ログイン", "ユーザー登録"])

    if menu == "ログイン":
        username = st.sidebar.text_input("ユーザー名")
        password = st.sidebar.text_input("パスワード", type='password')
        if st.sidebar.button("ログイン"):
            user = login_user(username, password)
            if user:
                st.session_state['logged_in_user'] = user
                st.rerun() 
            else:
                st.sidebar.error("ユーザー名またはパスワードが違います")
    
    elif menu == "ユーザー登録":
        new_user = st.sidebar.text_input("新しいユーザー名")
        new_password = st.sidebar.text_input("新しいパスワード", type='password')
        if st.sidebar.button("登録"):
            try:
                add_user(new_user, new_password)
                st.sidebar.success("ユーザー登録が完了しました！ログインしてください。")
            except sqlite3.IntegrityError:
                st.sidebar.error("そのユーザー名は既に使われています。")
            except Exception as e:
                st.sidebar.error(f"登録エラー: {e}")

    st.title("💡 Unspoken-Pain Finder")
    st.warning("機能を体験するには、サイドバーからログインまたはユーザー登録をしてください。")
