import json
import re
from typing import Any, Dict, List, Optional


class JSONParser:
    """
    通用JSON解析器。
    支持从包含Markdown代码块、前后缀文本的原始字符串中提取并修复JSON。
    截断修复基于纯语法括号栈追踪，不依赖任何特定业务字段。
    """

    @staticmethod
    def extract_json(raw_text: str) -> Optional[Dict[str, Any]]:
        """
        从原始文本中提取JSON对象。
        优先尝试直接解析，失败后依次尝试：
        1. 提取Markdown代码块内容
        2. 正则匹配首个{...}片段
        3. 通用截断修复
        
        Args:
            raw_text: 可能包含JSON的原始字符串
            
        Returns:
            解析成功返回dict，所有路径均失败返回None
        """
        if not raw_text or not isinstance(raw_text, str):
            return None

        text = raw_text.strip()
        if not text:
            return None

        # 路径1: 直接解析
        result = JSONParser._try_parse(text)
        if result is not None:
            return result

        # 路径2: 提取Markdown代码块
        code_block_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', text, re.DOTALL | re.IGNORECASE)
        if code_block_match:
            block_content = code_block_match.group(1).strip()
            result = JSONParser._try_parse(block_content)
            if result is not None:
                return result
            # 代码块内容也可能被截断，进入修复流程
            result = JSONParser._fix_truncated_json(block_content)
            if result is not None:
                return result

        # 路径3: 正则匹配首个 {...} 片段（贪婪匹配到最后一个}）
        brace_match = re.search(r'\{.*\}', text, re.DOTALL)
        if brace_match:
            candidate = brace_match.group(0)
            result = JSONParser._try_parse(candidate)
            if result is not None:
                return result
            result = JSONParser._fix_truncated_json(candidate)
            if result is not None:
                return result

        # 路径4: 对原始文本直接尝试截断修复（处理无代码块且无完整{}对的情况）
        result = JSONParser._fix_truncated_json(text)
        return result

    @staticmethod
    def _try_parse(s: str) -> Optional[Dict[str, Any]]:
        """安全尝试JSON解析，仅接受顶层为dict的结果"""
        try:
            result = json.loads(s)
            return result if isinstance(result, dict) else None
        except (json.JSONDecodeError, ValueError, TypeError):
            return None

    @staticmethod
    def _fix_truncated_json(json_str: str) -> Optional[Dict[str, Any]]:
        """
        通用截断JSON修复器。
        纯语法驱动，使用键值对感知的括号栈追踪 + 渐进式降级策略。
        不包含任何业务字段校验逻辑。
        """
        if not json_str or not json_str.strip():
            return None

        s = json_str.strip()
        if not s.startswith('{'):
            return None

        # --- 纯语法括号栈追踪 ---
        stack: List[str] = []
        in_string = False
        escape_next = False
        last_meaningful_char = ''

        i = 0
        while i < len(s):
            ch = s[i]

            if escape_next:
                escape_next = False
                i += 1
                continue

            if ch == '\\' and in_string:
                escape_next = True
                i += 1
                continue

            if ch == '"' and not escape_next:
                in_string = not in_string
                if not in_string:
                    last_meaningful_char = '"'
                i += 1
                continue

            if in_string:
                i += 1
                continue

            # 字符串外语法字符处理
            if ch == ':':
                last_meaningful_char = ':'
            elif ch == ',':
                last_meaningful_char = ','
            elif ch == '{':
                stack.append('{')
                last_meaningful_char = '{'
            elif ch == '[':
                stack.append('[')
                last_meaningful_char = '['
            elif ch == '}':
                if stack and stack[-1] == '{':
                    stack.pop()
                last_meaningful_char = '}'
            elif ch == ']':
                if stack and stack[-1] == '[':
                    stack.pop()
                last_meaningful_char = ']'
            elif ch not in ' \t\n\r':
                last_meaningful_char = ch

            i += 1

        # 栈为空说明括号已平衡，尝试直接解析
        if not stack:
            return JSONParser._try_parse(s)

        # --- 智能补全：按栈逆序生成闭合后缀 ---
        suffix_parts: List[str] = []
        for bracket in reversed(stack):
            suffix_parts.append('}' if bracket == '{' else ']')

        fixed_str = s + ''.join(suffix_parts)
        result = JSONParser._try_parse(fixed_str)
        if result is not None:
            return result

        # --- 渐进式降级：逐个移除栈顶元素重试 ---
        for trim_count in range(1, len(stack) + 1):
            trimmed_stack = stack[:-trim_count]
            retry_suffix = ['}' if b == '{' else ']' for b in reversed(trimmed_stack)]
            retry_str = s + ''.join(retry_suffix)
            result = JSONParser._try_parse(retry_str)
            if result is not None:
                return result

        return None


def parse_json(raw_text: str) -> Optional[Dict[str, Any]]:
    """
    解析JSON字符串的便捷函数。
    使用JSONParser类的extract_json方法。
    
    Args:
        raw_text (str): 包含JSON的原始文本
        
    Returns:
        Optional[Dict[str, Any]]: 解析成功的JSON字典，失败返回None
    """
    return JSONParser.extract_json(raw_text)
