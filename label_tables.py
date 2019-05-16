#!/usr/bin/env python

import fire
from sota_extractor.taskdb import TaskDB
from pathlib import Path
import json
import re
import pandas as pd
import sys
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, InvalidOperation
from collections import Counter


arxiv_url_re = re.compile(r"^https?://(www.)?arxiv.org/(abs|pdf|e-print)/(?P<arxiv_id>\d{4}\.[^./]*)(\.pdf)?$")

def get_sota_tasks(filename):
    db = TaskDB()
    db.load_tasks(filename)
    return db.tasks_with_sota()


def get_metadata(filename):
    with open(filename, "r") as f:
        j = json.load(f)
    metadata = {x["filename"]:x["caption"] for x in j}
    return metadata


def get_table(filename):
    try:
        return pd.read_csv(filename, header=None, dtype=str).fillna('')
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def get_tables(tables_dir):
    tables_dir = Path(tables_dir)
    all_metadata = {}
    all_tables = {}
    for metadata_filename in tables_dir.glob("*/metadata.json"):
        metadata = get_metadata(metadata_filename)
        basedir = metadata_filename.parent
        arxiv_id = basedir.name
        all_metadata[arxiv_id] = metadata
        all_tables[arxiv_id] = {t:get_table(basedir / t) for t in metadata}
    return all_metadata, all_tables


metric_na = ['-','']


# problematic values of metrics found in evaluation-tables.json
# F0.5, 70.14 (measured by Ge et al., 2018)
# Test Time, 0.33s/img
# Accuracy, 77,62%
# Electronics, 85,06
# BLEU-1, 54.60/55.55
# BLEU-4, 26.71/27.78
# MRPC, 78.6/84.4
# MRPC, 76.2/83.1
# STS, 78.9/78.6
# STS, 75.8/75.5
# BLEU score,41.0*
# BLEU score,28.5*
# SemEval 2007,**55.6**
# Senseval 2,**69.0**
# Senseval 3,**66.9**
# MAE, 2.42±0.01

## multiple times
# Number of params, 0.8B
# Number of params, 88M
# Parameters, 580k
# Parameters, 3.1m
# Params, 22M



float_value_re = re.compile(r"([+-]?\s*((\d{1,2}(,\d{3})+|\d+)(\.\d*)?|\.\d+)([eE][+-]?\d+)?)")
letters_re = re.compile("[^\W\d_]", re.UNICODE)

# float value possibly with std
metric_value_re = re.compile(float_value_re.pattern + r"(\s*±\s*" + float_value_re.pattern + ")?")
whitespace_re = re.compile(r"\s+")


def normalize_float_value(s):
    match = metric_value_re.search(s)
    if match:
        return whitespace_re.sub("", match.group(1)).replace(",", "")
    return '-'


def test_near(x, precise):
    for rounding in [ROUND_DOWN, ROUND_HALF_UP]:
        try:
            if x == precise.quantize(x, rounding=rounding):
                return True
        except InvalidOperation:
            pass
    return False


def fuzzy_match(metric, metric_value, target_value):
    metric_value = normalize_float_value(str(metric_value))
    if metric_value in metric_na:
        return False
    metric_value = Decimal(metric_value)

    for match in metric_value_re.findall(target_value):
        value = whitespace_re.sub("", match[0])
        value = Decimal(value)

        if test_near(metric_value, value):
            return True
        if test_near(metric_value.shift(2), value):
            return True
        if test_near(metric_value, value.shift(2)):
            return True

    return False
#
#    if metric_value in metric_na or target_value in metric_na:
#        return False
#    if metric_value != target_value and metric_value in target_value:
#        print(f"|{metric_value}|{target_value}|")
#    return metric_value in target_value


def match_metric(metric, tables, value):
    matching_tables = []
    for table in tables:
        for col in tables[table]:
            for row in tables[table][col]:
                if fuzzy_match(metric, value, row):
                    matching_tables.append(table)
                    break
            else:
                continue
            break

    return matching_tables


comparators = [
    test_near,
    lambda metric, target: test_near(metric.shift(2), target),
    lambda metric, target: test_near(metric, target.shift(2)),
    lambda metric, target: test_near(Decimal("1") - metric, target),
    lambda metric, target: test_near(Decimal("100") - metric.shift(2), target),
    lambda metric, target: test_near(Decimal("100") - metric, target.shift(2))
]


def empty_celltags_like(table):
    return = pd.DataFrame().reindex_like(table).fillna('')


def mark_with_best_comparator(task_name, dataset_name, metric_name, arxiv_id, table, values):
    max_hits = 0
    best_tags = None
    rows, cols = table.shape

    for comparator in comparators:
        hits = 0
        cell_tags = empty_celltags_like(table)
        for col in range(cols):
            for row in range(rows):
                for val in table.iloc[row, col]:
                    for record in values:
                        if comparator(record["normalized"], val):
                            hits += 1
                            tags = f"<sota>{record['value']}</sota>" +\
                                   f"<paper>{record['arxiv_id']}</paper>" +\
                                   f"<model>{record['model']}</model>" +\
                                   f"<metric>{metric_name}</metric>" +\
                                   f"<dataset>{dataset_name}</dataset>" +\
                                   f"<task>{task_name}</task>"
                            if arxiv_id == record["arxiv_id"]:
                                tags += "<this_paper>"
                            cell_tags.iloc[row, col] += tags
        if max_hits < hits:
            max_hits = hits
            best_tags = cell_tags

    return best_tags


def normalize_string(s):
    return s.lower.strip()


def match_str(a, b):
    return normalize_string(a) == normalize_string(b)


def mark_strings(table, tags, values):
    cell_tags = empty_celltags_like(table)
    beg, end = tags
    rows, cols = table.shape
    for col in range(cols):
            for row in range(rows):
                for s in values:
                    real = table.iloc[row, col]
                    if match_str(real, s):
                        cell_tags += f"{beg}{s}{end}"
    return cell_tags
    

metatables = {}
def match_many(output_dir, task_name, dataset_name, metric_name, tables, values):
    for arxiv_id in tables:
        for table in tables[arxiv_id]:
            best = mark_with_best_comparator(task_name, dataset_name, metric_name, arxiv_id, tables[arxiv_id][table], values)
            global metatables
            if best is not None:
                key = (arxiv_id, table)
                if key in metatables:
                    metatables[key] += best
                else:
                    metatables[key] = best


def normalize_metric(value):
    value = normalize_float_value(str(value))
    if value in metric_na:
        return Decimal("NaN")
    return Decimal(value)


def normalize_cell(cell):
    if len(letters_re.findall(cell)) > 2:
        return []
    matches = metric_value_re.findall(cell)
    matches = [normalize_float_value(match[0]) for match in matches]
    values = [Decimal(value) for value in matches]
    return values


def normalize_table(table):
    return table.applymap(normalize_cell)


# for each task with sota row
#     arxivs <- list of papers related to the task
#     for each (dataset_name, metric_name) of the task:
#         for each table in arxivs
#             for each fuzzy_comparator
#                 count number of task's sota rows found in the table using comparator
#             comparator <- comparator with the largest number of hits
#             if hits > hits_threshold:
#                 mark table with a given dataset_name and metric_name
#                 mark hit cells with sota-tag, model_name and paper_id
#                 if table.arxiv_id == paper_id: mark with this-tag


def label_tables(tasksfile, tables_dir, output, output_dir):
    output_dir = Path(output_dir)
    tasks = get_sota_tasks(tasksfile)
    metadata, tables = get_tables(tables_dir)

    arxivs_by_metrics = {}

    tables = {arxiv_id: {tab: normalize_table(tables[arxiv_id][tab]) for tab in tables[arxiv_id]} for arxiv_id in tables}

    for task in tasks:
        for dataset in task.datasets:
            for row in dataset.sota.rows:
                match = arxiv_url_re.match(row.paper_url)
                if match is not None:
                    arxiv_id = match.group("arxiv_id")
                    for metric in row.metrics:
                        arxivs_by_metrics.setdefault((task.name, dataset.name, metric), []).append(
                            dict(arxiv_id=arxiv_id, model=row.model_name, value=row.metrics[metric],
                                normalized=normalize_metric(row.metrics[metric])
                            )
                        )

    for task, dataset, metric in arxivs_by_metrics:
        records = arxivs_by_metrics[(task, dataset, metric)]
        tabs = {r["arxiv_id"]: tables[r["arxiv_id"]] for r in records if r["arxiv_id"] in tables}
        match_many(output_dir, task, dataset, metric, tabs, records)

    global metatables

    for (arxiv_id, table), best in metatables.items():
        out = output_dir / arxiv_id
        out.mkdir(parents=True, exist_ok=True)
        best.to_csv(out / table.replace("table", "celltags"), header=None, index=None)

    return
    tables_with_sota = []
    for task in tasks:
        for dataset in task.datasets:
            for row in dataset.sota.rows:
                # TODO: some results have more than one url, CoRR + journal / conference
                # check if we have the same results for both

                match = arxiv_url_re.match(row.paper_url)
                if match is not None:
                    arxiv_id = match.group("arxiv_id")
                    if arxiv_id not in tables:
                        print(f"No tables for {arxiv_id}. Skipping", file=sys.stderr)
                        continue

                    for metric in row.metrics:
                        #print(f"{metric}\t{row.metrics[metric]}")
                        #print((task.name, dataset.name, metric, row.model_name, row.metrics[metric], row.paper_url))
                        matching = match_metric(metric, tables[arxiv_id], row.metrics[metric])
                        if len(matching) == 1:
                            sota_table = matching[0]

                            tables_with_sota.append(
                                dict(
                                    task_name=task.name,
                                    dataset_name=dataset.name,
                                    metric_name=metric,
                                    model_name=row.model_name,
                                    metric_value=row.metrics[metric],
                                    paper_url=row.paper_url,
                                    table_caption=metadata[arxiv_id][sota_table],
                                    table_filename=f"{arxiv_id}/{sota_table}"
                                )
                            )
                        #if not matching:
                        #    print(f"{metric}, {row.metrics[metric]}, {arxiv_id}")
                        #print(f"{metric},{len(matching)}")
                        #if matching:
                        #    print((task.name, dataset.name, metric, row.model_name, row.metrics[metric], row.paper_url))
                        #    print(matching)
    pd.DataFrame(tables_with_sota).to_csv(output, index=None)



if __name__ == "__main__": fire.Fire(label_tables)
