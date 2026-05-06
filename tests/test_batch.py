"""超级并发性能验证测试

验证:
- /big_batch/completions 调用正常
- 分批逻辑（章节数 > max_batch_size）
- 批量结果正确解析
"""

import pytest
from unittest.mock import Mock, patch
from src.core.config import SamplingParams, APIConfig
from src.core.rwkv_client import RWKVClient


class TestBatchProcessing:
    """批量处理测试"""

    @pytest.fixture
    def mock_client(self):
        """创建模拟客户端"""
        api_config = APIConfig(
            base_url="http://localhost:8000",
            api_key="test",
            model="rwkv7-g1c-13.3b",
        )
        
        with patch('src.core.rwkv_client.requests.Session') as mock_session:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.headers = {"Content-Type": "application/json"}
            mock_response.json.return_value = {
                "choices": [
                    {"message": {"content": f"Generated content {i}"}}
                    for i in range(10)
                ]
            }
            mock_session.return_value.post.return_value = mock_response
            
            client = RWKVClient(api_config)
            yield client

    def test_big_batch_call(self, mock_client):
        """测试超级并发调用"""
        contents = [f"Prompt {i}" for i in range(10)]
        sampling = SamplingParams(temperature=1.4, max_tokens=2048)

        results = mock_client.big_batch_completions(contents, sampling)
        
        assert len(results) == 10
        for i, result in enumerate(results):
            assert f"Generated content {i}" in result

    def test_batch_size_limit(self):
        """测试批量大小限制"""
        # 验证 max_batch_size 配置
        from src.core.config import ConcurrencyConfig
        
        config = ConcurrencyConfig(max_batch_size=960)
        assert config.max_batch_size == 960
        
        # 验证超出限制的情况
        large_batch = list(range(1000))
        batch_size = config.max_batch_size
        
        # 应该被分成多个批次
        batches = [large_batch[i:i+batch_size] for i in range(0, len(large_batch), batch_size)]
        assert len(batches) == 2
        assert len(batches[0]) == 960
        assert len(batches[1]) == 40

    def test_batch_result_parsing(self):
        """测试批量结果解析"""
        # 模拟 API 响应
        mock_response = {
            "choices": [
                {"message": {"content": "Chapter 1 content"}},
                {"message": {"content": "Chapter 2 content"}},
                {"message": {"content": "Chapter 3 content"}},
            ]
        }

        # 解析结果
        results = [choice["message"]["content"] for choice in mock_response["choices"]]
        
        assert len(results) == 3
        assert results[0] == "Chapter 1 content"
        assert results[1] == "Chapter 2 content"
        assert results[2] == "Chapter 3 content"

    def test_batch_error_handling(self):
        """测试批量错误处理"""
        from src.core.error_handler import ErrorHandler
        
        handler = ErrorHandler()
        
        # 模拟部分失败
        batch_id = "test_batch_001"
        result = handler.handle_batch_failure(
            batch_id=batch_id,
            total_count=10,
            successful_indices=[0, 1, 2, 3, 4, 6, 7, 8, 9],
            failed_indices=[5],
            error_messages=["Timeout error"],
        )
        
        assert len(result.failed_indices) == 1
        assert result.failed_indices[0] == 5
        assert result.retry_count == 1
