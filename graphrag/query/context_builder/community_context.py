# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License

"""Community Context."""

import logging
import random
from typing import Any, cast

import pandas as pd
import tiktoken

from graphrag.model import CommunityReport, Entity
from graphrag.query.llm.text_utils import num_tokens

log = logging.getLogger(__name__)


def build_community_context(
    community_reports: list[CommunityReport],
    entities: list[Entity] | None = None,
    token_encoder: tiktoken.Encoding | None = None,
    use_community_summary: bool = True,
    column_delimiter: str = "|",
    shuffle_data: bool = True,
    include_community_rank: bool = False,
    min_community_rank: int = 0,
    community_rank_name: str = "rank",
    include_community_weight: bool = True,
    community_weight_name: str = "occurrence weight",
    normalize_community_weight: bool = True,
    max_tokens: int = 8000,
    single_batch: bool = True,
    context_name: str = "Reports",
    random_state: int = 86,
) -> tuple[str | list[str], dict[str, pd.DataFrame]]:
    """
    Prepare community report data table as context data for system prompt.

    If entities are provided, the community weight is calculated as the count of text units associated with entities within the community.

    The calculated weight is added as an attribute to the community reports and added to the context data table.
    """
    # 核心逻辑
    # 基于相关社区的报告，生成上下文

    # local search时entities参数为空，因此community_weight_name参数也不会被计算。
    # global search时触发，community_weight值是社区内实体关联的文本片段的数量。
    if (
        entities
        and len(community_reports) > 0
        and include_community_weight
        and (
            community_reports[0].attributes is None
            or community_weight_name not in community_reports[0].attributes
        )
    ):
        log.info("Computing community weights...")
        community_reports = _compute_community_weights(
            community_reports=community_reports,
            entities=entities,
            weight_attribute=community_weight_name,
            normalize=normalize_community_weight,
        )

    # 读取相关社区的报告，并过滤掉不符合要求的报告
    selected_reports = [
        report
        for report in community_reports
        if report.rank and report.rank >= min_community_rank
    ]
    if selected_reports is None or len(selected_reports) == 0:
        return ([], {})

    if shuffle_data:
        random.seed(random_state)
        random.shuffle(selected_reports)

    # 准备上下文中社区报告表的表头
    # add context header
    current_context_text = f"-----{context_name}-----" + "\n"

    # add header
    header = ["id", "title"]
    attribute_cols = (
        list(selected_reports[0].attributes.keys())
        if selected_reports[0].attributes
        else []
    )
    attribute_cols = [col for col in attribute_cols if col not in header]
    if not include_community_weight:
        attribute_cols = [col for col in attribute_cols if col != community_weight_name]
    header.extend(attribute_cols)
    header.append("summary" if use_community_summary else "content")
    if include_community_rank:
        header.append(community_rank_name)

    # 生成上下文中社区报告表的开头部分
    current_context_text += column_delimiter.join(header) + "\n"
    current_tokens = num_tokens(current_context_text, token_encoder)
    current_context_records = [header]
    all_context_text = []
    all_context_records = []

    # 对于每个报告
    for report in selected_reports:
        # 提取id与title到临时变量
        new_context = [
            report.short_id,
            report.title,
            *[
                str(report.attributes.get(field, "")) if report.attributes else ""
                for field in attribute_cols
            ],
        ]
        # 提取摘要，或者全文
        new_context.append(
            report.summary if use_community_summary else report.full_content
        )
        if include_community_rank:
            new_context.append(str(report.rank))

        # 加入分隔符，得到当前社区报告的上下文
        new_context_text = column_delimiter.join(new_context) + "\n"

        new_tokens = num_tokens(new_context_text, token_encoder)

        # 如果当前社区报告加上新的社区报告后超过了最大token数
        if current_tokens + new_tokens > max_tokens:
            # convert the current context records to pandas dataframe and sort by weight and rank if exist
            # 如果有社区报告上下文，则转换为pandas df并排序
            # 排序：1. weight 2. rank
            # >1 是因为current_context_records[0]是表头
            if len(current_context_records) > 1:
                record_df = _convert_report_context_to_df(
                    context_records=current_context_records[1:],
                    header=current_context_records[0],
                    weight_column=community_weight_name
                    if entities and include_community_weight
                    else None,
                    rank_column=community_rank_name if include_community_rank else None,
                )

            else:
                record_df = pd.DataFrame()

            # 将排序后的上下文转换回文本格式的数据表
            current_context_text = record_df.to_csv(index=False, sep=column_delimiter)

            if single_batch:
                # 返回上下文文本与数据表
                return current_context_text, {context_name.lower(): record_df}

            all_context_text.append(current_context_text)
            all_context_records.append(record_df)

            # start a new batch
            current_context_text = (
                f"-----{context_name}-----"
                + "\n"
                + column_delimiter.join(header)
                + "\n"
            )
            current_tokens = num_tokens(current_context_text, token_encoder)
            current_context_records = [header]
        # 如果当前社区报告加上新的社区报告后没有超过最大token数，则加入当前社区报告，继续循环
        else:
            current_context_text += new_context_text
            current_tokens += new_tokens
            current_context_records.append(new_context)

    # add the last batch if it has not been added
    # 用于处理所有社区报告都加入后仍未达到max_tokens的情况
    # 逻辑与注释同130行
    if current_context_text not in all_context_text:
        if len(current_context_records) > 1:
            record_df = _convert_report_context_to_df(
                context_records=current_context_records[1:],
                header=current_context_records[0],
                weight_column=community_weight_name
                if entities and include_community_weight
                else None,
                rank_column=community_rank_name if include_community_rank else None,
            )
        else:
            record_df = pd.DataFrame()
        all_context_records.append(record_df)
        current_context_text = record_df.to_csv(index=False, sep=column_delimiter)
        all_context_text.append(current_context_text)

    return all_context_text, {
        context_name.lower(): pd.concat(all_context_records, ignore_index=True)
    }


def _compute_community_weights(
    community_reports: list[CommunityReport],
    entities: list[Entity],
    weight_attribute: str = "occurrence",
    normalize: bool = True,
) -> list[CommunityReport]:
    """Calculate a community's weight as count of text units associated with entities within the community."""
    # 核心逻辑
    # 计算社区权重

    community_text_units = {}

    # 统计每个社区中的实体关联的文本片段id
    for entity in entities:
        if entity.community_ids:
            for community_id in entity.community_ids:
                if community_id not in community_text_units:
                    community_text_units[community_id] = []
                community_text_units[community_id].extend(entity.text_unit_ids)

    # 转换为set去重，得到每个社区关联的文本片段数量
    # 并加入到社区报告的属性中
    for report in community_reports:
        if not report.attributes:
            report.attributes = {}
        report.attributes[weight_attribute] = len(
            set(community_text_units.get(report.community_id, []))
        )

    # 归一化，除以最大值
    if normalize:
        # normalize by max weight
        all_weights = [
            report.attributes[weight_attribute]
            for report in community_reports
            if report.attributes
        ]
        max_weight = max(all_weights)
        for report in community_reports:
            if report.attributes:
                report.attributes[weight_attribute] = (
                    report.attributes[weight_attribute] / max_weight
                )
    return community_reports


def _rank_report_context(
    report_df: pd.DataFrame,
    weight_column: str | None = "occurrence weight",
    rank_column: str | None = "rank",
) -> pd.DataFrame:
    """Sort report context by community weight and rank if exist."""
    rank_attributes = []
    if weight_column:
        rank_attributes.append(weight_column)
        report_df[weight_column] = report_df[weight_column].astype(float)
    if rank_column:
        rank_attributes.append(rank_column)
        report_df[rank_column] = report_df[rank_column].astype(float)
    if len(rank_attributes) > 0:
        report_df.sort_values(by=rank_attributes, ascending=False, inplace=True)
    return report_df


def _convert_report_context_to_df(
    context_records: list[list[str]],
    header: list[str],
    weight_column: str | None = None,
    rank_column: str | None = None,
) -> pd.DataFrame:
    """Convert report context records to pandas dataframe and sort by weight and rank if exist."""
    record_df = pd.DataFrame(
        context_records,
        columns=cast(Any, header),
    )
    return _rank_report_context(
        report_df=record_df,
        weight_column=weight_column,
        rank_column=rank_column,
    )
