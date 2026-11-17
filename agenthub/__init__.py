"""AgentHub · 企业级智能体开发与运行支撑平台（裁剪版实现）。

模块划分：
    config / db / models / schemas   基础设施与数据模型
    dag                               DAG 校验、分层布局、LangGraph 编译
    builtin_tools                     内置工具（征信/财报/司法/报告）
    llm                               Provider 与模型别名解析、调用与降级
    kb                                知识库切片与检索
    report                            HTML 全景分析报告渲染
    runner                            运行执行器（LangGraph + 事件流）
    api/*                             REST 接口
"""

__version__ = "0.1.0"
