#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
语义检索模块 - 为 AI Agent 提供语义检索功能

功能：
1. 会话历史语义检索（conversations 表）
2. 知识库语义检索（knowledge_index 表）
3. 向量嵌入（sentence-transformers / TF-IDF 回退）
4. 增量索引更新
5. 与知识库脚本完全兼容

依赖：
    pip install sentence-transformers numpy
"""

import sqlite3
import json
import hashlib
import threading
import time
import re
import os
from typing import List, Dict, Optional, Tuple, Any
from datetime import datetime
from pathlib import Path
# ===== 强制使用国内 HuggingFace 镜像 =====
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"
# ===== 尝试导入依赖库 =====
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    print("[语义检索] numpy 未安装，请运行: pip install numpy")

try:
    from sentence_transformers import SentenceTransformer
    HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    HAS_SENTENCE_TRANSFORMERS = False
    print("[语义检索] sentence-transformers 未安装，使用 TF-IDF 回退")
    print("  建议运行: pip install sentence-transformers")

try:
    import jieba
    HAS_JIEBA = True
except ImportError:
    HAS_JIEBA = False
    print("[语义检索] jieba 未安装，使用简单分词回退")

# ===== 配置 =====
DEFAULT_MODEL = 'BAAI/bge-small-zh-v1.5'
DEFAULT_EMBEDDING_DIM = 384
MAX_TEXT_LENGTH = 500
RETRIEVAL_TOP_K = 5
MIN_SIMILARITY = 0.25


class EmbeddingEngine:
    """嵌入引擎 - 支持多种后端"""
    
    def __init__(self, model_name: str =  None, logger=None):
        if model_name is None:
            model_name = os.environ.get("EMBEDDING_MODEL", DEFAULT_MODEL)
        
        self.model_name = model_name
        self.logger = logger or (lambda x: None)
        self.model = None
        self.dim = DEFAULT_EMBEDDING_DIM
        self._initialized = False
        self._init_engine()
        
    def _ensure_initialized(self):  
        if not self._initialized:
            self._init_engine()
            self._initialized = True
    
    def _init_engine(self):
        """初始化嵌入引擎"""
        if HAS_SENTENCE_TRANSFORMERS:
            try:
                # 尝试加载模型

                
                self.model = SentenceTransformer(
                    self.model_name,
                    device='cpu'
                )
                self.dim = self.model.get_sentence_embedding_dimension()
                self.logger(f"[嵌入引擎] 加载模型成功: {self.model_name} (维度: {self.dim})")
                self._initialized = True 
                return
            except Exception as e:
                self.logger(f"[嵌入引擎] 模型加载失败: {e}，使用 TF-IDF 回退")
        
        # 回退到 TF-IDF
        self.model = None
        self.dim = 128
        self._initialized = True 
        self.logger("[嵌入引擎] 使用 TF-IDF 回退方案 (维度: 128)")
    
    def encode(self, text: str) -> Optional[np.ndarray]:
        """编码文本为向量"""
        self._ensure_initialized() 
        if not text or len(text.strip()) < 2:
            return None
        
        # 截断过长的文本
        if len(text) > MAX_TEXT_LENGTH:
            text = text[:MAX_TEXT_LENGTH]
        
        if self.model is not None:
            try:
                return self.model.encode(text, normalize_embeddings=True)
            except Exception as e:
                self.logger(f"[嵌入引擎] 编码失败: {e}")
                return None
        
        # TF-IDF 回退
        return self._tfidf_encode(text)
    
    def _tfidf_encode(self, text: str) -> np.ndarray:
        """TF-IDF 简化编码"""
        if not HAS_NUMPY:
            return None
        
        # 分词
        if HAS_JIEBA:
            words = jieba.lcut(text)
        else:
            # 简单分词：中文按字，英文按词
            words = re.findall(r'[\u4e00-\u9fff]|[a-zA-Z]{2,}', text)
        
        if not words:
            return np.zeros(self.dim)
        
        # 词频统计
        from collections import Counter
        word_count = Counter(words)
        total_words = len(words)
        
        # 构建向量（取前 self.dim 个词）
        vec = np.zeros(self.dim)
        for i, (word, count) in enumerate(word_count.items()):
            if i >= self.dim:
                break
            vec[i] = count / total_words
        
        # 归一化
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        
        return vec
    
    def get_dimension(self) -> int:
        return self.dim
    
    def is_available(self) -> bool:
        return self.model is not None or HAS_NUMPY


class SemanticRetriever:
    """语义检索器 - 主类"""
    
    def __init__(self, db_path: str = "agent_memory.db", logger=None):
        self.db_path = db_path
        self.logger = logger or (lambda x: None)
        
        # 初始化嵌入引擎
        self.engine = EmbeddingEngine(logger=self.logger)
        
        # 初始化数据库表
        self._init_tables()
        
        # 缓存
        self._embedding_cache = {}
        self._cache_max_size = 200
        
        # 索引状态
        self._indexed = False
        self._index_lock = threading.Lock()
        
        # 统计
        self._stats = {
            'conversations_indexed': 0,
            'knowledge_indexed': 0,
            'last_index_time': None,
            'total_searches': 0
        }
        
        
        self.logger("[语义检索] 初始化完成")
    
    def _init_tables(self):
        """初始化向量存储表"""
        with sqlite3.connect(self.db_path) as conn:
            # 会话向量表（关联 conversations 表）
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversation_vectors (
                    conversation_id INTEGER PRIMARY KEY,
                    content TEXT NOT NULL,
                    embedding BLOB,
                    embedding_model TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                )
            """)
            
            # 知识库向量表（关联 knowledge_index 表）
            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_vectors (
                    knowledge_id INTEGER PRIMARY KEY,
                    content TEXT NOT NULL,
                    embedding BLOB,
                    embedding_model TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (knowledge_id) REFERENCES knowledge_index(id) ON DELETE CASCADE
                )
            """)
            
            # 创建索引
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_model ON conversation_vectors(embedding_model)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_know_model ON knowledge_vectors(embedding_model)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversation_vectors(updated_at)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_know_updated ON knowledge_vectors(updated_at)")
            except Exception as e:
                pass
            
            conn.commit()
    
    def _vector_to_blob(self, vec: np.ndarray) -> bytes:
        """将向量转换为 BLOB"""
        if vec is None or not HAS_NUMPY:
            return b''
        return vec.astype(np.float32).tobytes()
    
    def _blob_to_vector(self, blob: bytes) -> Optional[np.ndarray]:
        """将 BLOB 转换为向量"""
        if not blob or not HAS_NUMPY:
            return None
        try:
            return np.frombuffer(blob, dtype=np.float32)
        except Exception:
            return None
    
    def _get_cache_key(self, text: str) -> str:
        """生成缓存键"""
        return hashlib.md5(text[:200].encode('utf-8')).hexdigest()
    
    def _cached_encode(self, text: str) -> Optional[np.ndarray]:
        """带缓存的编码"""
        if not text or len(text.strip()) < 2:
            return None
        
        key = self._get_cache_key(text)
        if key in self._embedding_cache:
            return self._embedding_cache[key]
        
        vec = self.engine.encode(text)
        
        # 更新缓存
        if vec is not None:
            self._embedding_cache[key] = vec
            if len(self._embedding_cache) > self._cache_max_size:
                # 删除最早的条目
                oldest_key = next(iter(self._embedding_cache))
                del self._embedding_cache[oldest_key]
        
        return vec
    
    def _cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """计算余弦相似度"""
        if vec1 is None or vec2 is None or not HAS_NUMPY:
            return 0.0
        if len(vec1) != len(vec2):
            return 0.0
        try:
            # 如果已经归一化，直接点积
            return float(np.dot(vec1, vec2))
        except Exception:
            return 0.0
    
    # ============================================================
    # 索引构建
    # ============================================================
    
    def build_index(self, force: bool = False, max_records: int = 1000):
        """构建所有向量索引
        
        Args:
            force: 是否强制重建所有索引
            max_records: 单次最大索引数量
        """
        with self._index_lock:
            if self._indexed and not force:
                self.logger("[语义检索] 索引已存在，跳过构建")
                return
                
            if not force:
                with sqlite3.connect(self.db_path) as conn:
                    count = conn.execute("SELECT COUNT(*) FROM conversation_vectors").fetchone()[0]
                    if count > 0:
                        self._indexed = True
                        self.logger(f"[语义检索] 已有 {count} 条向量，跳过构建")
                        return
                
            self.logger("[语义检索] 开始构建索引...")
            start_time = time.time()
            
            # 索引会话历史
            conv_count = self._index_conversations(force, max_records)
            
            # 索引知识库
            know_count = self._index_knowledge(force, max_records)
            
            self._stats['conversations_indexed'] = conv_count
            self._stats['knowledge_indexed'] = know_count
            self._stats['last_index_time'] = datetime.now().isoformat()
            self._indexed = True
            
            elapsed = time.time() - start_time
            self.logger(f"[语义检索] 索引完成: 会话 {conv_count} 条, 知识 {know_count} 条, 耗时 {elapsed:.2f}s")
    
    def _index_conversations(self, force: bool = False, max_records: int = 1000) -> int:
        """索引会话历史"""
        with sqlite3.connect(self.db_path) as conn:
            # 构建查询
            if force:
                sql = """
                    SELECT id, content FROM conversations 
                    WHERE content NOT LIKE '[心跳]%' 
                    AND content NOT LIKE '[系统]%'
                    AND content NOT LIKE '[心跳机制]%'
                    AND length(content) > 10
                    ORDER BY id DESC
                    LIMIT ?
                """
                cursor = conn.execute(sql, (max_records,))
            else:
                sql = """
                    SELECT c.id, c.content 
                    FROM conversations c
                    LEFT JOIN conversation_vectors v ON c.id = v.conversation_id
                    WHERE (v.conversation_id IS NULL)
                    AND c.content NOT LIKE '[心跳]%' 
                    AND c.content NOT LIKE '[系统]%'
                    AND c.content NOT LIKE '[心跳机制]%'
                    AND length(c.content) > 10
                    ORDER BY c.id DESC
                    LIMIT ?
                """
                cursor = conn.execute(sql, (max_records,))
            
            rows = cursor.fetchall()
            if not rows:
                return 0
            
            count = 0
            model_name = self.engine.model_name if self.engine.model else 'tfidf'
            
            for row in rows:
                conv_id, content = row
                if not content or len(content.strip()) < 5:
                    continue
                
                vec = self._cached_encode(content)
                if vec is None:
                    continue
                
                blob = self._vector_to_blob(vec)
                
                # 插入或更新
                conn.execute("""
                    INSERT OR REPLACE INTO conversation_vectors 
                    (conversation_id, content, embedding, embedding_model, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (conv_id, content[:300], blob, model_name))
                count += 1
            
            conn.commit()
            return count
    
    def _index_knowledge(self, force: bool = False, max_records: int = 500) -> int:
        """索引知识库"""
        # 检查知识库表是否存在
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_index'"
            )
            if not cursor.fetchone():
                self.logger("[语义检索] 知识库表不存在，跳过索引")
                return 0
            
            # 构建查询
            if force:
                sql = """
                    SELECT id, title, summary, text_keywords, type_keywords 
                    FROM knowledge_index 
                    ORDER BY id DESC
                    LIMIT ?
                """
                cursor = conn.execute(sql, (max_records,))
            else:
                sql = """
                    SELECT k.id, k.title, k.summary, k.text_keywords, k.type_keywords 
                    FROM knowledge_index k
                    LEFT JOIN knowledge_vectors v ON k.id = v.knowledge_id
                    WHERE (v.knowledge_id IS NULL)
                    ORDER BY k.id DESC
                    LIMIT ?
                """
                cursor = conn.execute(sql, (max_records,))
            
            rows = cursor.fetchall()
            if not rows:
                return 0
            
            count = 0
            model_name = self.engine.model_name if self.engine.model else 'tfidf'
            
            for row in rows:
                know_id, title, summary, text_keywords, type_keywords = row
                
                # 组合内容用于编码
                content_parts = [title or '']
                if summary:
                    content_parts.append(summary)
                if text_keywords:
                    content_parts.append(text_keywords)
                if type_keywords:
                    content_parts.append(type_keywords)
                
                content = ' '.join(content_parts)
                if len(content.strip()) < 5:
                    continue
                
                vec = self._cached_encode(content)
                if vec is None:
                    continue
                
                blob = self._vector_to_blob(vec)
                
                conn.execute("""
                    INSERT OR REPLACE INTO knowledge_vectors 
                    (knowledge_id, content, embedding, embedding_model, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (know_id, content[:300], blob, model_name))
                count += 1
            
            conn.commit()
            return count
    
    def update_index(self, max_records: int = 100) -> Dict:
        """增量更新索引（只索引新数据）"""
        with self._index_lock:
            conv_count = self._index_conversations(False, max_records)
            know_count = self._index_knowledge(False, max_records)
            
            self._stats['conversations_indexed'] += conv_count
            self._stats['knowledge_indexed'] += know_count
            self._stats['last_index_time'] = datetime.now().isoformat()
            
            return {
                'success': True,
                'conversations_indexed': conv_count,
                'knowledge_indexed': know_count,
                'message': f'新增索引: 会话 {conv_count} 条, 知识 {know_count} 条'
            }
    
    # ============================================================
    # 检索功能
    # ============================================================
    
    def retrieve_conversations(self, query: str, top_k: int = RETRIEVAL_TOP_K,
                               min_similarity: float = MIN_SIMILARITY) -> List[Dict]:
        """从会话历史中检索相关内容"""
        if not query or len(query.strip()) < 2:
            return []
        
        self._stats['total_searches'] += 1
        
        query_vec = self._cached_encode(query)
        if query_vec is None:
            return []
        
        with sqlite3.connect(self.db_path) as conn:
            # 获取所有已索引的会话
            cursor = conn.execute("""
                SELECT v.conversation_id, v.content, v.embedding, 
                       c.role, c.timestamp
                FROM conversation_vectors v
                JOIN conversations c ON v.conversation_id = c.id
                ORDER BY v.updated_at DESC
                LIMIT 2000
            """)
            rows = cursor.fetchall()
        
        if not rows:
            return []
        
        # 计算相似度
        results = []
        for row in rows:
            conv_id, content, blob, role, timestamp = row
            vec = self._blob_to_vector(blob)
            if vec is None:
                continue
            
            similarity = self._cosine_similarity(query_vec, vec)
            if similarity >= min_similarity:
                results.append({
                    'id': conv_id,
                    'role': role or 'unknown',
                    'content': content,
                    'timestamp': timestamp,
                    'similarity': round(similarity, 4),
                    'source': 'conversation'
                })
        
        # 按相似度降序排序
        results.sort(key=lambda x: x['similarity'], reverse=True)
        return results[:top_k]
    
    def retrieve_knowledge(self, query: str, top_k: int = RETRIEVAL_TOP_K,
                           min_similarity: float = MIN_SIMILARITY) -> List[Dict]:
        """从知识库中检索相关内容"""
        if not query or len(query.strip()) < 2:
            return []
        
        self._stats['total_searches'] += 1
        
        query_vec = self._cached_encode(query)
        if query_vec is None:
            return []
        
        with sqlite3.connect(self.db_path) as conn:
            # 检查 knowledge_index 表是否存在
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_index'"
            )
            if not cursor.fetchone():
                return []
            
            # 获取所有已索引的知识
            cursor = conn.execute("""
                SELECT v.knowledge_id, v.content, v.embedding,
                       k.title, k.summary, k.type_keywords, k.file_path
                FROM knowledge_vectors v
                JOIN knowledge_index k ON v.knowledge_id = k.id
                ORDER BY v.updated_at DESC
                LIMIT 500
            """)
            rows = cursor.fetchall()
        
        if not rows:
            return []
        
        # 计算相似度
        results = []
        for row in rows:
            know_id, content, blob, title, summary, type_keywords, file_path = row
            vec = self._blob_to_vector(blob)
            if vec is None:
                continue
            
            similarity = self._cosine_similarity(query_vec, vec)
            if similarity >= min_similarity:
                results.append({
                    'id': know_id,
                    'title': title or '未命名',
                    'summary': summary or '',
                    'type_keywords': type_keywords or '',
                    'file_path': file_path,
                    'content': content,
                    'similarity': round(similarity, 4),
                    'source': 'knowledge'
                })
        
        # 按相似度降序排序
        results.sort(key=lambda x: x['similarity'], reverse=True)
        return results[:top_k]

    def retrieve_memories(self, query: str, top_k: int = RETRIEVAL_TOP_K,
                          min_similarity: float = MIN_SIMILARITY) -> List[Dict]:
        """从常驻记忆文件中检索相关内容（带去重）"""
        if not query or len(query.strip()) < 2:
            return []
        
        memory_file = Path("./memories.json")
        if not memory_file.exists():
            return []
        
        try:
            with open(memory_file, 'r', encoding='utf-8') as f:
                memories = json.load(f)
        except:
            return []
        
        if not memories:
            return []
        
        query_vec = self._cached_encode(query)
        if query_vec is None:
            return []
        
        results = []
        for item in memories:
            content = item.get('value', '')
            if not content:
                continue
            
            vec = self._cached_encode(content)
            if vec is None:
                continue
            
            similarity = self._cosine_similarity(query_vec, vec)
            if similarity >= min_similarity:
                weight = item.get('weight', 5)  # 默认权重 5
                results.append({
                    'id': item.get('id'),
                    'value': content,
                    'similarity': round(similarity, 4),
                    'weight': weight,
                    'score': round(similarity * (weight / 5), 4),  # ← 加权分数
                    'source': 'memory'
                })
        
        # 按加权分数排序
        results.sort(key=lambda x: x['score'], reverse=True)
        
        # ===== 去重：删除语义高度相似的内容 =====
        deduped = []
        for r in results:
            # 检查是否与已有结果高度相似（相似度 > 0.9）
            is_duplicate = False
            for existing in deduped:
                # 计算两条记忆之间的相似度
                existing_vec = self._cached_encode(existing['value'])
                if existing_vec is not None:
                    dup_sim = self._cosine_similarity(
                        self._cached_encode(r['value']),
                        existing_vec
                    )
                    if dup_sim is not None and dup_sim > 0.85:
                        is_duplicate = True
                        break
            if not is_duplicate:
                deduped.append(r)
        
        return deduped[:top_k]
    
    def retrieve_combined(self, query: str, top_k: int = RETRIEVAL_TOP_K,
                          min_similarity: float = MIN_SIMILARITY) -> Dict:
        """同时从会话和知识库检索"""
        conv_results = self.retrieve_conversations(query, top_k, min_similarity)
        know_results = self.retrieve_knowledge(query, top_k, min_similarity)
        mem_results = self.retrieve_memories(query, top_k, min_similarity)
        return {
            'query': query,
            'conversations': conv_results,
            'knowledge': know_results,
            'memories': mem_results,  
            'total': len(conv_results) + len(know_results)+ len(mem_results),
            'timestamp': datetime.now().isoformat()
        }
    
    # ============================================================
    # 格式化输出
    # ============================================================
    
    def format_for_prompt(self, results: Dict, max_length: int = 300) -> str:
        """格式化检索结果为提示词注入"""
        if not results or results.get('total', 0) == 0:
            return ""
        
        parts = []
        # 记忆（放在最前面）
        if results.get('memories'):  
            parts.append("\n## 相关常驻记忆") 
            for i, r in enumerate(results['memories'], 1):
                content = r.get('value', '')
                parts.append(f"{i}. {content}")
            parts.append("") 
        
        # 会话历史
        if results.get('conversations'):
            parts.append("\n## 相关历史对话 (语义检索)")
            for i, r in enumerate(results['conversations'], 1):
                role = "用户" if r.get('role') == 'user' else "AI"
                content = r.get('content', '')
                sim = r.get('similarity', 0)
                parts.append(f"{i}. [{role}] {content} (相关度: {sim:.2f})")
        
        # 知识库
        if results.get('knowledge'):
            parts.append("\n## 相关知识库条目 (语义检索)")
            for i, r in enumerate(results['knowledge'], 1):
                title = r.get('title', '未命名')
                summary = r.get('summary', '')
                sim = r.get('similarity', 0)
                parts.append(f"{i}. [{title}] {summary} (相关度: {sim:.2f})")
        
        return "\n".join(parts)
    
    # ============================================================
    # 统计和状态
    # ============================================================
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        with sqlite3.connect(self.db_path) as conn:
            conv_count = conn.execute("SELECT COUNT(*) FROM conversation_vectors").fetchone()[0]
            know_count = conn.execute("SELECT COUNT(*) FROM knowledge_vectors").fetchone()[0]
            
            # 检查知识库表是否存在
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_index'"
            )
            has_knowledge = cursor.fetchone() is not None
        
        return {
            'conversation_vectors': conv_count,
            'knowledge_vectors': know_count,
            'has_knowledge_table': has_knowledge,
            'model': self.engine.model_name if self.engine.model else 'tfidf',
            'dimension': self.engine.dim,
            'cache_size': len(self._embedding_cache),
            'indexed': self._indexed,
            'stats': self._stats
        }
    
    def clear_cache(self):
        """清除缓存"""
        self._embedding_cache.clear()
        self.logger("[语义检索] 缓存已清除")
    
    def rebuild_all(self):
        """重建所有索引"""
        self.clear_cache()
        
        # 清空向量表
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM conversation_vectors")
            conn.execute("DELETE FROM knowledge_vectors")
            conn.commit()
        
        self._indexed = False
        self.build_index(force=True)
    
    # ============================================================
    # 自动更新（后台线程）
    # ============================================================
    
    def start_auto_update(self, interval: int = 300):
        """启动自动更新线程"""
        if hasattr(self, '_update_thread') and self._update_thread and self._update_thread.is_alive():
            return
        
        self._stop_update = False
        
        def update_loop():
            self.logger(f"[语义检索] 自动更新已启动，间隔 {interval}s")
            while not self._stop_update:
                time.sleep(interval)
                if not self._stop_update:
                    try:
                        result = self.update_index(max_records=50)
                        if result.get('conversations_indexed', 0) > 0 or result.get('knowledge_indexed', 0) > 0:
                            self.logger(f"[语义检索] 自动更新: {result['message']}")
                    except Exception as e:
                        self.logger(f"[语义检索] 自动更新失败: {e}")
        
        self._update_thread = threading.Thread(target=update_loop, daemon=True)
        self._update_thread.start()
    
    def stop_auto_update(self):
        """停止自动更新"""
        self._stop_update = True
        if hasattr(self, '_update_thread') and self._update_thread:
            self._update_thread.join(timeout=5)


# ============================================================
# 命令行测试
# ============================================================

def main():
    """命令行测试"""
    import argparse
    
    parser = argparse.ArgumentParser(description='语义检索测试')
    parser.add_argument('--db', default='agent_memory.db', help='数据库路径')
    parser.add_argument('--query', required=True, help='查询文本')
    parser.add_argument('--top', type=int, default=3, help='返回结果数量')
    parser.add_argument('--build', action='store_true', help='构建索引')
    parser.add_argument('--stats', action='store_true', help='显示统计信息')
    
    args = parser.parse_args()
    
    # 初始化检索器
    retriever = SemanticRetriever(db_path=args.db)
    
    if args.build:
        print("构建索引...")
        retriever.build_index(force=True)
        print("索引构建完成")
    
    if args.stats:
        print("\n=== 统计信息 ===")
        stats = retriever.get_stats()
        print(f"会话向量数: {stats['conversation_vectors']}")
        print(f"知识向量数: {stats['knowledge_vectors']}")
        print(f"模型: {stats['model']}")
        print(f"维度: {stats['dimension']}")
        print(f"缓存大小: {stats['cache_size']}")
    
    # 执行检索
    print(f"\n=== 检索: {args.query} ===")
    
    results = retriever.retrieve_combined(args.query, top_k=args.top)
    
    print(f"\n会话历史 (找到 {len(results['conversations'])} 条):")
    for r in results['conversations']:
        print(f"  [{r['similarity']:.3f}] {r['content'][:100]}...")
    
    print(f"\n知识库 (找到 {len(results['knowledge'])} 条):")
    for r in results['knowledge']:
        print(f"  [{r['similarity']:.3f}] {r['title']}: {r['summary'][:80]}...")
    
    # 格式化输出
    formatted = retriever.format_for_prompt(results)
    if formatted:
        print("\n=== 格式化输出 (注入提示词) ===")
        print(formatted)


if __name__ == "__main__":
    main()
