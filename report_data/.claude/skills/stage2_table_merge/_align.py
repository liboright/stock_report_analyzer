import sys
sys.path.insert(0, r'D:\quant\report_data\.claude\skills\stage2_table_merge')
from llm_logger import get_logger
logger = get_logger('贵州茅台')
cid = logger.log_start(agent_name='aligner', task_description='test', metadata={'k':'v'})
print('CID=' + cid)
