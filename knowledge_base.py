#!/usr/bin/env python3
"""
知识库管理系统 - Knowledge Base Manager
功能：
1. 知识库管理：索引 + 知识文本的增删查改
2. 记忆变量管理：AI自主维护的记忆缓存（文件存储）
3. 提供指令供AI通过主脚本调用
"""

import json
import sqlite3
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict


# ============================================================================
# 数据结构定义
# ============================================================================

@dataclass
class KnowledgeIndex:
    """知识索引数据结构"""
    id: int
    title: str
    type_keywords: str
    text_keywords: str
    summary: str
    file_path: str
    file_size: int
    created_at: str
    updated_at: str
    access_count: int
    last_accessed: str
    tags: str
    metadata: str

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================================
# 记忆存储 - 文件存储
# ============================================================================

class MemoryStore:
    """记忆变量存储 - 使用 JSON 文件"""
    
    def __init__(self, file_path: str = "./memories.json"):
        self.file_path = Path(file_path)
        self._data: List[Dict] = []
        self._load()
    
    def _load(self):
        """从文件加载记忆"""
        if self.file_path.exists():
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    self._data = json.load(f)
            except:
                self._data = []
        else:
            self._data = []
    
    def _save(self):
        """保存记忆到文件"""
        with open(self.file_path, 'w', encoding='utf-8') as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
    
    def add(self, value: str) -> Dict:
        """添加记忆"""
        if not value or not value.strip():
            return {'success': False, 'error': '内容不能为空'}
        
        max_id = max([item.get('id', 0) for item in self._data]) if self._data else 0
        new_id = max_id + 1
        
        self._data.append({
            'id': new_id,
            'value': value.strip(),
            'created_at': datetime.now().isoformat()
        })
        self._save()
        
        return {
            'success': True,
            'id': new_id,
            'message': f'记忆添加成功 (ID: {new_id})'
        }
    
    def update(self, var_id: int, value: str) -> Dict:
        """更新记忆"""
        if not value or not value.strip():
            return {'success': False, 'error': '内容不能为空'}
        
        for item in self._data:
            if item['id'] == var_id:
                item['value'] = value.strip()
                item['updated_at'] = datetime.now().isoformat()
                self._save()
                return {
                    'success': True,
                    'message': f'记忆 ID {var_id} 更新成功'
                }
        return {'success': False, 'error': f'记忆 ID {var_id} 不存在'}
    
    def delete(self, var_id: int) -> Dict:
        """删除记忆"""
        for i, item in enumerate(self._data):
            if item['id'] == var_id:
                del self._data[i]
                self._save()
                return {
                    'success': True,
                    'message': f'记忆 ID {var_id} 删除成功'
                }
        return {'success': False, 'error': f'记忆 ID {var_id} 不存在'}
    
    def list_all(self, limit: int = 50) -> List[Dict]:
        """列出所有记忆"""
        return self._data[:limit]
    
    def get_injection_content(self) -> str:
        """获取注入内容"""
        if not self._data:
            return ""
        
        lines = ["\n## 我的记忆\n"]
        for item in self._data:
            lines.append(f" [{item['id']}]{item['value']}")
        
        return "\n".join(lines)
    
    def count(self) -> int:
        """获取记忆数量"""
        return len(self._data)


# ============================================================================
# 知识库管理器
# ============================================================================

class KnowledgeBaseManager:
    """知识库管理器 - 负责索引和知识文本的管理"""
    
    def __init__(self, db_path: str = "agent_memory.db", knowledge_dir: str = "./knowledge_base"):
        self.db_path = db_path
        self.knowledge_dir = Path(knowledge_dir)
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        
        self._init_db()
        self._init_knowledge_dir()
        
        # 记忆变量 - 使用文件存储
        self.memory = MemoryStore("./memories.json")
        
        # 内存缓存：类型关键词 -> 索引列表
        self._cache: Dict[str, List[KnowledgeIndex]] = {}
        self._cache_timestamp = 0
        self._cache_ttl = 300
        
        print(f"[知识库] 初始化完成，知识目录: {self.knowledge_dir}")
        print(f"[记忆] 加载了 {self.memory.count()} 条记忆")
    
    def _init_db(self):
        """初始化数据库表"""
        with sqlite3.connect(self.db_path) as conn:
            # 知识索引表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_index (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    type_keywords TEXT NOT NULL,
                    text_keywords TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    file_path TEXT NOT NULL UNIQUE,
                    file_size INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    access_count INTEGER DEFAULT 0,
                    last_accessed DATETIME,
                    tags TEXT DEFAULT '[]',
                    metadata TEXT DEFAULT '{}'
                )
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_type_keywords 
                ON knowledge_index(type_keywords)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_text_keywords 
                ON knowledge_index(text_keywords)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_updated_at 
                ON knowledge_index(updated_at DESC)
            """)
            
            # 删除旧的 prompt_variables 表（改用文件存储）
            conn.execute("DROP TABLE IF EXISTS prompt_variables")
            
            conn.commit()
    
    def _init_knowledge_dir(self):
        """初始化知识存储目录"""
        subdirs = ['documents', 'code', 'data', 'templates', 'other']
        for sub in subdirs:
            (self.knowledge_dir / sub).mkdir(exist_ok=True)
        
        for sub in subdirs:
            keep_file = self.knowledge_dir / sub / '.gitkeep'
            if not keep_file.exists():
                keep_file.touch()
    
    def _generate_file_path(self, title: str, type_keywords: str) -> str:
        """生成知识文本文件路径"""
        type_map = {
            '文档': 'documents',
            '代码': 'code',
            '数据': 'data',
            '模板': 'templates',
        }
        
        subdir = 'other'
        for key, dir_name in type_map.items():
            if key in type_keywords:
                subdir = dir_name
                break
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_title = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fa5]', '_', title)[:50]
        filename = f"{timestamp}_{safe_title}.txt"
        
        return str(self.knowledge_dir / subdir / filename)
    
    def _validate_index_data(self, data: dict) -> Tuple[bool, str]:
        """验证索引数据"""
        required_fields = ['title', 'type_keywords', 'text_keywords', 'summary']
        for field in required_fields:
            if not data.get(field, '').strip():
                return False, f"缺少必填字段: {field}"
        
        if len(data['summary']) > 200:
            return False, "摘要长度不能超过200字"
        
        return True, ""
    
    def _execute_query(self, sql: str, params: tuple = (), fetch_one: bool = False, 
                       fetch_all: bool = False) -> Any:
        """执行数据库查询"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(sql, params)
            if fetch_one:
                row = cursor.fetchone()
                return dict(row) if row else None
            if fetch_all:
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
            return cursor.rowcount
    
    def _execute_write(self, sql: str, params: tuple = ()) -> int:
        """执行写操作"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.lastrowid
    
    # ========================================================================
    # 知识库 - 增删查改
    # ========================================================================
    
    def add_knowledge(self, title: str, type_keywords: str, text_keywords: str,
                      summary: str, content: str, tags: List[str] = None,
                      metadata: Dict = None) -> Dict:
        """添加知识条目"""
        valid, msg = self._validate_index_data({
            'title': title,
            'type_keywords': type_keywords,
            'text_keywords': text_keywords,
            'summary': summary
        })
        if not valid:
            return {'success': False, 'error': msg}
        
        existing = self._execute_query(
            "SELECT id, title FROM knowledge_index WHERE title = ?",
            (title,), fetch_one=True
        )
        if existing:
            return {'success': False, 'error': f'知识标题 "{title}" 已存在'}
        
        file_path = self._generate_file_path(title, type_keywords)
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            file_size = os.path.getsize(file_path)
            tags_json = json.dumps(tags or [], ensure_ascii=False)
            metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
            
            idx = self._execute_write("""
                INSERT INTO knowledge_index 
                (title, type_keywords, text_keywords, summary, file_path, 
                 file_size, tags, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (title, type_keywords, text_keywords, summary, file_path,
                  file_size, tags_json, metadata_json))
            
            self._cache.clear()
            self._cache_timestamp = 0
            
            return {
                'success': True,
                'id': idx,
                'file_path': file_path,
                'message': f'知识 "{title}" 添加成功'
            }
            
        except Exception as e:
            if os.path.exists(file_path):
                os.remove(file_path)
            return {'success': False, 'error': str(e)}
    
    def get_knowledge(self, knowledge_id: int) -> Optional[Dict]:
        """获取知识索引信息和内容"""
        index = self._execute_query(
            "SELECT * FROM knowledge_index WHERE id = ?",
            (knowledge_id,), fetch_one=True
        )
        
        if not index:
            return None
        
        self._execute_write("""
            UPDATE knowledge_index 
            SET access_count = access_count + 1, 
                last_accessed = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (knowledge_id,))
        
        file_path = index.get('file_path')
        content = ''
        if file_path and os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception as e:
                content = f'[读取失败: {e}]'
        
        index['tags'] = json.loads(index.get('tags', '[]'))
        index['metadata'] = json.loads(index.get('metadata', '{}'))
        index['content'] = content
        
        return index
    
    def search_knowledge(self, query: str, search_type: str = 'all',
                        limit: int = 20) -> List[Dict]:
        """搜索知识库"""
        if not query.strip():
            return []
        
        keywords = re.findall(r'[\u4e00-\u9fa5]+|[a-zA-Z]{2,}', query)
        if not keywords:
            return []
        
        conditions = []
        params = []
        
        if search_type in ['all', 'title']:
            for kw in keywords[:3]:
                conditions.append("title LIKE ?")
                params.append(f"%{kw}%")
        
        if search_type in ['all', 'type']:
            for kw in keywords[:3]:
                conditions.append("type_keywords LIKE ?")
                params.append(f"%{kw}%")
        
        if search_type in ['all', 'text']:
            for kw in keywords[:3]:
                conditions.append("text_keywords LIKE ?")
                params.append(f"%{kw}%")
        
        if not conditions:
            return []
        
        where_clause = " OR ".join(conditions)
        sql = f"""
            SELECT id, title, type_keywords, text_keywords, summary,
                   file_path, file_size, access_count, updated_at
            FROM knowledge_index
            WHERE {where_clause}
            ORDER BY access_count DESC, updated_at DESC
            LIMIT ?
        """
        
        results = self._execute_query(sql, tuple(params + [limit]), fetch_all=True)
        
        for row in results:
            row['tags'] = json.loads(row.get('tags', '[]'))
            row['metadata'] = json.loads(row.get('metadata', '{}'))
        
        return results
    
    def update_knowledge(self, knowledge_id: int, **kwargs) -> Dict:
        """更新知识条目"""
        existing = self.get_knowledge(knowledge_id)
        if not existing:
            return {'success': False, 'error': f'知识ID {knowledge_id} 不存在'}
        
        update_fields = []
        params = []
        
        updatable_fields = ['title', 'type_keywords', 'text_keywords', 'summary', 'tags', 'metadata']
        for field in updatable_fields:
            if field in kwargs and kwargs[field] is not None:
                update_fields.append(f"{field} = ?")
                if field in ['tags', 'metadata']:
                    params.append(json.dumps(kwargs[field], ensure_ascii=False))
                else:
                    params.append(kwargs[field])
        
        if 'content' in kwargs and kwargs['content'] is not None:
            file_path = existing.get('file_path')
            if file_path:
                try:
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(kwargs['content'])
                except Exception as e:
                    return {'success': False, 'error': f'更新文件失败: {e}'}
        
        if not update_fields:
            return {'success': False, 'error': '没有要更新的字段'}
        
        update_fields.append("updated_at = CURRENT_TIMESTAMP")
        params.append(knowledge_id)
        
        sql = f"UPDATE knowledge_index SET {', '.join(update_fields)} WHERE id = ?"
        
        try:
            self._execute_write(sql, tuple(params))
            self._cache.clear()
            self._cache_timestamp = 0
            return {
                'success': True,
                'message': f'知识ID {knowledge_id} 更新成功'
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def delete_knowledge(self, knowledge_id: int, permanent: bool = False) -> Dict:
        """删除知识条目"""
        existing = self.get_knowledge(knowledge_id)
        if not existing:
            return {'success': False, 'error': f'知识ID {knowledge_id} 不存在'}
        
        file_path = existing.get('file_path')
        
        try:
            self._execute_write(
                "DELETE FROM knowledge_index WHERE id = ?",
                (knowledge_id,)
            )
            
            if permanent and file_path and os.path.exists(file_path):
                os.remove(file_path)
                dir_path = os.path.dirname(file_path)
                if os.path.exists(dir_path) and not os.listdir(dir_path):
                    os.rmdir(dir_path)
            
            self._cache.clear()
            self._cache_timestamp = 0
            
            return {
                'success': True,
                'message': f'知识ID {knowledge_id} 删除成功'
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def list_knowledge(self, type_keywords: str = None, limit: int = 50,
                      offset: int = 0) -> List[Dict]:
        """列出知识库条目"""
        sql = """
            SELECT id, title, type_keywords, text_keywords, summary,
                   file_size, access_count, updated_at
            FROM knowledge_index
        """
        params = []
        
        if type_keywords:
            sql += " WHERE type_keywords LIKE ?"
            params.append(f"%{type_keywords}%")
        
        sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        results = self._execute_query(sql, tuple(params), fetch_all=True)
        for row in results:
            row['tags'] = json.loads(row.get('tags', '[]'))
        
        return results
    
    def get_knowledge_count(self, type_keywords: str = None) -> int:
        """获取知识库条目数量"""
        sql = "SELECT COUNT(*) as count FROM knowledge_index"
        params = []
        
        if type_keywords:
            sql += " WHERE type_keywords LIKE ?"
            params.append(f"%{type_keywords}%")
        
        result = self._execute_query(sql, tuple(params), fetch_one=True)
        return result.get('count', 0) if result else 0
    
    def export_knowledge(self, knowledge_ids: List[int]) -> Dict:
        """导出知识"""
        results = []
        for kid in knowledge_ids:
            data = self.get_knowledge(kid)
            if data:
                results.append(data)
        
        if not results:
            return {'success': False, 'error': '没有可导出的知识'}
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        export_file = self.knowledge_dir / f"export_{timestamp}.json"
        
        try:
            with open(export_file, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            return {
                'success': True,
                'file_path': str(export_file),
                'count': len(results),
                'message': f'导出 {len(results)} 条知识到 {export_file}'
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def import_knowledge(self, file_path: str) -> Dict:
        """导入知识"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if not isinstance(data, list):
                return {'success': False, 'error': '导入数据格式错误'}
            
            success_count = 0
            failed_count = 0
            errors = []
            
            for item in data:
                result = self.add_knowledge(
                    title=item.get('title', ''),
                    type_keywords=item.get('type_keywords', ''),
                    text_keywords=item.get('text_keywords', ''),
                    summary=item.get('summary', ''),
                    content=item.get('content', ''),
                    tags=item.get('tags', []),
                    metadata=item.get('metadata', {})
                )
                if result.get('success'):
                    success_count += 1
                else:
                    failed_count += 1
                    errors.append(f"{item.get('title', '未知')}: {result.get('error')}")
            
            return {
                'success': True,
                'imported': success_count,
                'failed': failed_count,
                'errors': errors[:10]
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    # ========================================================================
    # 缓存管理
    # ========================================================================
    
    def load_to_cache(self, type_keywords: str, force_refresh: bool = False) -> List[Dict]:
        """根据类型关键词加载知识到缓存"""
        import time
        
        cache_key = type_keywords
        if not force_refresh and cache_key in self._cache:
            if time.time() - self._cache_timestamp < self._cache_ttl:
                return [idx.to_dict() for idx in self._cache[cache_key]]
        
        results = self._execute_query(
            "SELECT * FROM knowledge_index WHERE type_keywords LIKE ? ORDER BY access_count DESC",
            (f"%{type_keywords}%",), fetch_all=True
        )
        
        indices = []
        for row in results:
            idx = KnowledgeIndex(
                id=row['id'],
                title=row['title'],
                type_keywords=row['type_keywords'],
                text_keywords=row['text_keywords'],
                summary=row['summary'],
                file_path=row['file_path'],
                file_size=row['file_size'],
                created_at=row['created_at'],
                updated_at=row['updated_at'],
                access_count=row['access_count'],
                last_accessed=row['last_accessed'],
                tags=row['tags'],
                metadata=row['metadata']
            )
            indices.append(idx)
        
        self._cache[cache_key] = indices
        self._cache_timestamp = time.time()
        
        return [idx.to_dict() for idx in indices]
    
    def get_cached_content(self, type_keywords: str) -> str:
        """获取缓存中的知识内容（用于注入提示词）"""
        indices = self.load_to_cache(type_keywords)
        if not indices:
            return ""
        
        content_parts = []
        for idx in indices:
            file_path = idx.get('file_path')
            if file_path and os.path.exists(file_path):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        text = f.read()
                    content_parts.append(f"## {idx['title']}\n{text}\n")
                except:
                    pass
        
        return "\n".join(content_parts)
    
    def clear_cache(self):
        """清除缓存"""
        self._cache.clear()
        self._cache_timestamp = 0
    
    # ========================================================================
    # 统计和状态
    # ========================================================================
    
    def get_stats(self) -> Dict:
        """获取知识库统计信息"""
        total_knowledge = self.get_knowledge_count()
        
        type_stats = self._execute_query("""
            SELECT type_keywords, COUNT(*) as count
            FROM knowledge_index
            GROUP BY type_keywords
        """, fetch_all=True)
        
        cache_stats = {
            'cached_types': len(self._cache),
            'total_cached': sum(len(indices) for indices in self._cache.values())
        }
        
        return {
            'total_knowledge': total_knowledge,
            'type_stats': type_stats,
            'memory_count': self.memory.count(),
            'cache_stats': cache_stats,
            'knowledge_dir_size': self._get_dir_size(self.knowledge_dir)
        }
    
    def _get_dir_size(self, path: Path) -> int:
        """计算目录大小"""
        total = 0
        for f in path.rglob('*'):
            if f.is_file():
                total += f.stat().st_size
        return total
    
    def cleanup(self):
        """清理资源"""
        self.clear_cache()


# ============================================================================
# 与主脚本集成的指令接口
# ============================================================================

class KnowledgeBaseInterface:
    """知识库接口 - 提供给AI调用的指令处理"""
    
    def __init__(self, db_path: str = "agent_memory.db", knowledge_dir: str = "./knowledge_base"):
        self.manager = KnowledgeBaseManager(db_path, knowledge_dir)
    
    def execute_command(self, command: Dict) -> str:
        """执行知识库指令"""
        action = command.get('action', '').upper()
        payload = command.get('payload', {})
        
        handlers = {
            'KNOWLEDGE_ADD': self._handle_knowledge_add,
            'KNOWLEDGE_GET': self._handle_knowledge_get,
            'KNOWLEDGE_SEARCH': self._handle_knowledge_search,
            'KNOWLEDGE_UPDATE': self._handle_knowledge_update,
            'KNOWLEDGE_DELETE': self._handle_knowledge_delete,
            'KNOWLEDGE_LIST': self._handle_knowledge_list,
            'KNOWLEDGE_EXPORT': self._handle_knowledge_export,
            'KNOWLEDGE_IMPORT': self._handle_knowledge_import,
            'KNOWLEDGE_LOAD_CACHE': self._handle_knowledge_load_cache,
            'KNOWLEDGE_STATS': self._handle_knowledge_stats,
            'VAR_ADD': self._handle_var_add,
            'VAR_UPDATE': self._handle_var_update,
            'VAR_DELETE': self._handle_var_delete,
            'VAR_LIST': self._handle_var_list,
        }
        
        handler = handlers.get(action)
        if handler:
            result = handler(payload)
            return self._format_result(result)
        else:
            return f'未知指令: {action}。支持: {", ".join(handlers.keys())}'
    
    def _format_result(self, result) -> str:
        """格式化结果为字符串"""
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            if 'message' in result:
                return result['message']
            if 'error' in result:
                return f"错误: {result['error']}"
            return json.dumps(result, ensure_ascii=False, indent=2)
        if isinstance(result, list):
            if not result:
                return "无结果"
            return json.dumps(result, ensure_ascii=False, indent=2)
        return str(result)
    
    # ========================================================================
    # 知识库操作处理器
    # ========================================================================
    
    def _handle_knowledge_add(self, payload: Dict) -> Dict:
        required = ['title', 'type_keywords', 'text_keywords', 'summary', 'content']
        for field in required:
            if field not in payload:
                return {'error': f'缺少必填字段: {field}'}
        
        return self.manager.add_knowledge(
            title=payload['title'],
            type_keywords=payload['type_keywords'],
            text_keywords=payload['text_keywords'],
            summary=payload['summary'],
            content=payload['content'],
            tags=payload.get('tags', []),
            metadata=payload.get('metadata', {})
        )
    
    def _handle_knowledge_get(self, payload: Dict) -> Dict:
        if 'id' not in payload:
            return {'error': '缺少字段: id'}
        result = self.manager.get_knowledge(payload['id'])
        return result if result else {'error': '知识不存在'}
    
    def _handle_knowledge_search(self, payload: Dict) -> List[Dict]:
        if 'query' not in payload:
            return {'error': '缺少字段: query'}
        return self.manager.search_knowledge(
            query=payload['query'],
            search_type=payload.get('search_type', 'all'),
            limit=payload.get('limit', 20)
        )
    
    def _handle_knowledge_update(self, payload: Dict) -> Dict:
        if 'id' not in payload:
            return {'error': '缺少字段: id'}
        
        update_data = {k: v for k, v in payload.items() 
                      if k in ['title', 'type_keywords', 'text_keywords', 
                              'summary', 'content', 'tags', 'metadata']}
        if not update_data:
            return {'error': '没有可更新的字段'}
        
        return self.manager.update_knowledge(payload['id'], **update_data)
    
    def _handle_knowledge_delete(self, payload: Dict) -> Dict:
        if 'id' not in payload:
            return {'error': '缺少字段: id'}
        return self.manager.delete_knowledge(
            payload['id'],
            permanent=payload.get('permanent', False)
        )
    
    def _handle_knowledge_list(self, payload: Dict) -> List[Dict]:
        return self.manager.list_knowledge(
            type_keywords=payload.get('type_keywords'),
            limit=payload.get('limit', 50),
            offset=payload.get('offset', 0)
        )
    
    def _handle_knowledge_export(self, payload: Dict) -> Dict:
        if 'ids' not in payload or not payload['ids']:
            return {'error': '缺少字段: ids 或为空'}
        return self.manager.export_knowledge(payload['ids'])
    
    def _handle_knowledge_import(self, payload: Dict) -> Dict:
        if 'file_path' not in payload:
            return {'error': '缺少字段: file_path'}
        return self.manager.import_knowledge(payload['file_path'])
    
    def _handle_knowledge_load_cache(self, payload: Dict) -> Dict:
        if 'type_keywords' not in payload:
            return {'error': '缺少字段: type_keywords'}
        
        results = self.manager.load_to_cache(
            payload['type_keywords'],
            force_refresh=payload.get('force_refresh', False)
        )
        
        return {
            'success': True,
            'count': len(results),
            'message': f'已加载 {len(results)} 条知识到缓存'
        }
    
    def _handle_knowledge_stats(self, payload: Dict) -> Dict:
        return self.manager.get_stats()
    
    # ========================================================================
    # 记忆变量操作处理器 - 精简版
    # ========================================================================
    
    def _handle_var_add(self, payload: Dict) -> Dict:
        """添加记忆"""
        if 'value' not in payload:
            return {'error': '缺少字段: value'}
        return self.manager.memory.add(payload['value'])
    
    def _handle_var_update(self, payload: Dict) -> Dict:
        """更新记忆"""
        if 'id' not in payload or 'value' not in payload:
            return {'error': '缺少字段: id 或 value'}
        return self.manager.memory.update(payload['id'], payload['value'])
    
    def _handle_var_delete(self, payload: Dict) -> Dict:
        """删除记忆（支持单个或批量）"""
        if 'id' in payload:
            return self.manager.memory.delete(payload['id'])
        elif 'ids' in payload and payload['ids']:
            # 批量删除
            deleted = []
            failed = []
            for var_id in payload['ids']:
                result = self.manager.memory.delete(var_id)
                if result.get('success'):
                    deleted.append(var_id)
                else:
                    failed.append(var_id)
            return {
                'success': True if deleted else False,
                'deleted': deleted,
                'failed': failed,
                'message': f'成功删除 {len(deleted)} 条' + (f'，失败: {failed}' if failed else '')
            }
        return {'error': '缺少字段: id 或 ids'}
    
    def _handle_var_list(self, payload: Dict) -> List[Dict]:
        """列出所有记忆"""
        return self.manager.memory.list_all(limit=payload.get('limit', 50))


if __name__ == "__main__":
    print("知识库管理模块 - 请通过主脚本调用")
