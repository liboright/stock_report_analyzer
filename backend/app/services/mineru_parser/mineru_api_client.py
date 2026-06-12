"""MinerU 在线 API 客户端。

从 `D:/quant/report_gen/report_generator/parser/mineru_api_client.py` 复制并改造：
- `MINERU_API_KEY` / `MINERU_API_BASE` 不再硬编码为模块常量，改为构造注入
  （由 `pdf_parse_service.py` 从 `app.config.get_settings()` 传入）
- 其余流程（upload → poll → download zip → 抽 markdown）保持不变
- 2026-06 新增 `BatchMinerUClient`：单次 `POST /v4/file-urls/batch` 提交 N 份 PDF，
  单 batch_id 轮询所有状态，N 个 zip 一次性回拉。`parse_split_pipeline` 走此入口。

API 流程（单文件 `MinerUAPIClient`）：
1. POST  /v4/file-urls/batch        拿 batch_id + 预签名 OSS 上传 URL
2. PUT   <file_url>                  上传原始 PDF 字节
3. GET   /v4/extract-results/batch/{id}  轮询 state∈{done, failed}，done 即停
4. GET   <full_zip_url>              下载 MinerU 产出 zip，解压到 output_dir，
                                     取首个 .md 文件内容

API 流程（批量 `BatchMinerUClient`）：
1. POST  /v4/file-urls/batch        提交 N 个文件 → batch_id + N 个 file_urls
2. PUT   <file_url> × N              并发上传（ThreadPoolExecutor）
3. GET   /v4/extract-results/batch/{id}  轮询 extract_result[].state，任一 done 即收
                                     （继续轮询直到全部 done / 任一 failed）
4. GET   <full_zip_url> × N          下载 N 个 zip，按 data_id 索引返回
"""
from __future__ import annotations

import io
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from .exceptions import PDFParserError


# 与 MinerU 服务约定的默认 base（仅作 fallback；真实使用永远走 settings 注入）
_DEFAULT_API_BASE = "https://mineru.net/api"


class MinerUAPIClient:
    """MinerU 在线 API 客户端 —— 只负责 PDF→MD。"""

    def __init__(
        self,
        pdf_path: str,
        api_key: str,
        api_base: str = _DEFAULT_API_BASE,
    ):
        if not api_key or api_key == "placeholder-replace-me":
            raise PDFParserError(
                "MINERU_API_KEY 未配置或为占位符；请在 .env 设置后重试，或使用 mock 模式。"
            )

        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise PDFParserError(f"PDF 文件不存在: {pdf_path}")

        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.output_dir: Optional[Path] = None
        self.markdown_content: Optional[str] = None
        self.batch_id: Optional[str] = None
        self.file_url: Optional[str] = None

    # ---------- 公共 API ----------

    def convert(self, output_dir: Optional[str] = None) -> str:
        """执行 PDF→Markdown 转换。返回原始 markdown（未做标题归一）。"""
        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            self.output_dir = self.pdf_path.parent / "mineru_output"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        try:
            self._upload_file()
            self._wait_for_completion()
            self.markdown_content = self._get_result()
        except PDFParserError:
            raise
        except Exception as e:
            raise PDFParserError(f"MinerU 在线 API 转换失败: {e}") from e

        return self.markdown_content

    def get_output_dir(self) -> Optional[Path]:
        return self.output_dir

    # ---------- 内部步骤 ----------

    def _upload_file(self) -> None:
        url = f"{self.api_base}/v4/file-urls/batch"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        data = {
            "files": [{"name": self.pdf_path.name, "data_id": str(uuid.uuid4())}],
            "model_version": "vlm",
            "language": "ch",
            "enable_formula": True,
            "enable_table": True,
        }

        resp = requests.post(url, headers=headers, json=data, timeout=60)
        if resp.status_code != 200:
            raise PDFParserError(f"获取上传 URL 失败: {resp.status_code} - {resp.text}")
        body = resp.json()
        if body.get("code") != 0:
            raise PDFParserError(f"获取上传 URL 失败: {body.get('msg', '未知错误')}")

        self.batch_id = body["data"]["batch_id"]
        self.file_url = body["data"]["file_urls"][0]

        with open(self.pdf_path, "rb") as f:
            upload_resp = requests.put(self.file_url, data=f, timeout=300)
        if upload_resp.status_code != 200:
            raise PDFParserError(f"上传文件失败: {upload_resp.status_code}")

    def _wait_for_completion(self, max_wait_time: int = 600, poll_interval: int = 5) -> None:
        url = f"{self.api_base}/v4/extract-results/batch/{self.batch_id}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        start = time.time()
        while time.time() - start < max_wait_time:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code != 200:
                time.sleep(poll_interval)
                continue
            body = resp.json()
            if body.get("code") != 0:
                raise PDFParserError(f"查询任务失败: {body.get('msg', '未知错误')}")
            extract = body.get("data", {}).get("extract_result", [])
            if extract:
                state = extract[0].get("state")
                if state == "done":
                    return
                if state == "failed":
                    raise PDFParserError(f"解析失败: {extract[0].get('err_msg', '未知错误')}")
            time.sleep(poll_interval)
        raise PDFParserError(f"等待解析完成超时 ({max_wait_time} 秒)")

    def _get_result(self) -> str:
        url = f"{self.api_base}/v4/extract-results/batch/{self.batch_id}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        resp = requests.get(url, headers=headers, timeout=60)
        if resp.status_code != 200:
            raise PDFParserError(f"获取结果失败: {resp.status_code}")
        body = resp.json()
        if body.get("code") != 0:
            raise PDFParserError(f"获取结果失败: {body.get('msg', '未知错误')}")

        task = body.get("data", {}).get("extract_result", [{}])[0]
        zip_url = task.get("full_zip_url")
        if not zip_url:
            raise PDFParserError("未找到结果文件 URL")

        zip_resp = requests.get(zip_url, timeout=60)
        if zip_resp.status_code != 200:
            raise PDFParserError(f"下载结果失败: {zip_resp.status_code}")

        md_content: Optional[str] = None
        with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
            zf.extractall(str(self.output_dir))
            for name in zf.namelist():
                if name.endswith(".md"):
                    with zf.open(name) as f:
                        md_content = f.read().decode("utf-8")
                    break

        if not md_content:
            raise PDFParserError("未在结果中找到 Markdown 文件")
        return md_content


def convert_mineru(
    pdf_path: str,
    api_key: str,
    api_base: str = _DEFAULT_API_BASE,
    output_dir: Optional[str] = None,
) -> str:
    """便捷函数：直接调一次 convert。"""
    client = MinerUAPIClient(pdf_path, api_key=api_key, api_base=api_base)
    return client.convert(output_dir)


class BatchMinerUClient:
    """MinerU 批量客户端：单次提交 N 份 PDF，单 batch_id 轮询，N 个结果回拉。

    适用场景：一家公司多年报告的"业务+财务"一次性 batch 提交（项目主用）。
    单文件场景也可走本类（items 长度 1 即可），但 `MinerUAPIClient` 是更轻的封装。
    """

    def __init__(
        self,
        api_key: str,
        api_base: str = _DEFAULT_API_BASE,
    ):
        if not api_key or api_key == "placeholder-replace-me":
            raise PDFParserError(
                "MINERU_API_KEY 未配置或为占位符；请在 .env 设置后重试，或使用 mock 模式。"
            )
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.batch_id: Optional[str] = None
        # 仅内部用：data_id → file_url（POST /v4/file-urls/batch 拿到后填）
        self._file_urls: Dict[str, str] = {}

    def submit_and_wait(
        self,
        items: List[Tuple[Path, str]],
        *,
        model_version: str = "vlm",
        language: str = "ch",
        enable_formula: bool = True,
        enable_table: bool = True,
        max_wait_time: int = 3600,
        poll_interval: int = 5,
        upload_concurrency: int = 4,
    ) -> Dict[str, str]:
        """一次性提交 N 份 PDF，轮询直到全部完成，返回 {data_id: markdown}。

        Args:
            items: [(local_pdf_path, data_id), ...]，data_id 须全局唯一（用于回查）
            model_version/language/enable_*: MinerU 任务参数（透传 API）
            max_wait_time: 总轮询超时秒数（默认 1 小时）
            poll_interval: 轮询间隔秒
            upload_concurrency: PUT 上传并发数

        Returns:
            {data_id: markdown_content}，长度 == len(items)

        Raises:
            PDFParserError 任一文件 state==failed 时立即抛（含 data_id 和 err_msg）
        """
        if not items:
            return {}
        # 预检：每个 PDF 存在
        for pdf, data_id in items:
            if not Path(pdf).exists():
                raise PDFParserError(f"PDF 不存在: {pdf} (data_id={data_id})")

        try:
            self._apply_for_upload_urls(
                items,
                model_version=model_version,
                language=language,
                enable_formula=enable_formula,
                enable_table=enable_table,
            )
            self._upload_all(items, concurrency=upload_concurrency)
            states = self._poll_all(
                [data_id for _, data_id in items],
                max_wait_time=max_wait_time,
                poll_interval=poll_interval,
            )
            return self._download_all_zips(items, states)
        except PDFParserError:
            raise
        except Exception as e:
            raise PDFParserError(f"MinerU 批量 API 转换失败: {e}") from e

    # ---------- 内部步骤 ----------

    def _apply_for_upload_urls(
        self,
        items: List[Tuple[Path, str]],
        *,
        model_version: str,
        language: str,
        enable_formula: bool,
        enable_table: bool,
    ) -> None:
        """POST /v4/file-urls/batch 申请 N 个上传 URL（spec：单次 ≤ 50）。"""
        url = f"{self.api_base}/v4/file-urls/batch"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        data = {
            "files": [
                {"name": Path(pdf).name, "data_id": data_id}
                for pdf, data_id in items
            ],
            "model_version": model_version,
            "language": language,
            "enable_formula": enable_formula,
            "enable_table": enable_table,
        }
        if len(items) > 50:
            raise PDFParserError(
                f"单次 batch 文件数 {len(items)} 超过 MinerU 限制 50，请分批"
            )

        resp = requests.post(url, headers=headers, json=data, timeout=60)
        if resp.status_code != 200:
            raise PDFParserError(f"申请上传 URL 失败: {resp.status_code} - {resp.text}")
        body = resp.json()
        if body.get("code") != 0:
            raise PDFParserError(f"申请上传 URL 失败: {body.get('msg', '未知错误')}")

        self.batch_id = body["data"]["batch_id"]
        file_urls: List[str] = body["data"]["file_urls"]
        if len(file_urls) != len(items):
            raise PDFParserError(
                f"MinerU 返回 file_urls 数量 {len(file_urls)} != 入参 {len(items)}"
            )
        # 按入参顺序对齐：MinerU 按 files 数组顺序返回 file_urls
        for (_, data_id), file_url in zip(items, file_urls):
            self._file_urls[data_id] = file_url

    def _upload_all(
        self, items: List[Tuple[Path, str]], *, concurrency: int
    ) -> None:
        """并发 PUT 上传 N 个 PDF 到各自的 file_url（spec：上传时无 Content-Type）。"""

        def _put_one(pdf_path: Path, data_id: str) -> Tuple[str, int]:
            file_url = self._file_urls[data_id]
            with open(pdf_path, "rb") as f:
                resp = requests.put(file_url, data=f, timeout=600)
            return data_id, resp.status_code

        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            futures = [
                ex.submit(_put_one, pdf, data_id) for pdf, data_id in items
            ]
            for fut in as_completed(futures):
                data_id, status_code = fut.result()
                if status_code != 200:
                    raise PDFParserError(
                        f"上传失败: data_id={data_id} status={status_code}"
                    )

    def _poll_all(
        self,
        data_ids: List[str],
        *,
        max_wait_time: int,
        poll_interval: int,
    ) -> Dict[str, dict]:
        """轮询 batch_id，按 data_id 索引每个文件状态。返回 {data_id: extract_result}。

        退出条件：
        - 全部 state==done → 返回
        - 任一 state==failed → 抛 PDFParserError
        - 超时 → 抛 PDFParserError
        """
        url = f"{self.api_base}/v4/extract-results/batch/{self.batch_id}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        target = set(data_ids)
        start = time.time()
        last_summary = ""
        while time.time() - start < max_wait_time:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code != 200:
                time.sleep(poll_interval)
                continue
            body = resp.json()
            if body.get("code") != 0:
                raise PDFParserError(f"查询任务失败: {body.get('msg', '未知错误')}")

            extract_list = body.get("data", {}).get("extract_result", [])
            states: Dict[str, dict] = {}
            for item in extract_list:
                did = item.get("data_id")
                if did in target:
                    states[did] = item
            if states:
                summary = ",".join(
                    f"{did}:{st.get('state','?')}" for did, st in states.items()
                )
                if summary != last_summary:
                    last_summary = summary
            # 检失败
            for did, item in states.items():
                if item.get("state") == "failed":
                    raise PDFParserError(
                        f"解析失败: data_id={did} err={item.get('err_msg', '未知')}"
                    )
            # 检全部 done
            if all(states.get(d, {}).get("state") == "done" for d in target):
                return states
            time.sleep(poll_interval)
        raise PDFParserError(
            f"等待 MinerU 解析超时 ({max_wait_time}s)，最后状态: {last_summary}"
        )

    def _download_all_zips(
        self,
        items: List[Tuple[Path, str]],
        states: Dict[str, dict],
    ) -> Dict[str, str]:
        """下载每个 done 文件的 full_zip_url，按 data_id 装配 markdown。"""
        out_dir_for: Dict[str, Path] = {
            data_id: Path(pdf).parent / "mineru_output" for pdf, data_id in items
        }
        seen_dirs: set = set()
        results: Dict[str, str] = {}
        for data_id, item in states.items():
            if item.get("state") != "done":
                continue
            zip_url = item.get("full_zip_url")
            if not zip_url:
                raise PDFParserError(f"data_id={data_id} 缺 full_zip_url")
            zip_resp = requests.get(zip_url, timeout=120)
            if zip_resp.status_code != 200:
                raise PDFParserError(
                    f"下载结果失败: data_id={data_id} status={zip_resp.status_code}"
                )
            out_dir = out_dir_for[data_id]
            if out_dir not in seen_dirs:
                out_dir.mkdir(parents=True, exist_ok=True)
                seen_dirs.add(out_dir)
            md_content: Optional[str] = None
            with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
                zf.extractall(str(out_dir))
                for name in zf.namelist():
                    if name.endswith(".md"):
                        with zf.open(name) as f:
                            md_content = f.read().decode("utf-8")
                        break
            if not md_content:
                raise PDFParserError(f"data_id={data_id} 未在结果 zip 中找到 .md")
            results[data_id] = md_content
        return results


def convert_mineru_batch(
    items: List[Tuple[str, str]],
    api_key: str,
    api_base: str = _DEFAULT_API_BASE,
    **kwargs,
) -> Dict[str, str]:
    """便捷函数：批量提交 (pdf_path, data_id) 列表，返回 {data_id: markdown}。"""
    client = BatchMinerUClient(api_key=api_key, api_base=api_base)
    return client.submit_and_wait(
        [(Path(p), d) for p, d in items], **kwargs
    )
