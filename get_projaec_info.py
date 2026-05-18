import argparse
import re

import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
import pandas as pd
import requests

from datetime import date


def get_project_info(user, project, name, item, date_key, token=""):
    header = {
        "Accept": "application/vnd.github.v3.star+json"
    }
    if token:
        header.update({
            "Authorization": f"token {token}",
        })
    data_list = []
    page = 0
    date_pat = re.compile("\d{4}-\d{2}-\d{2}")
    while True:
        page += 1
        url = f"https://api.github.com/repos/{user}/{project}/{item}?page={page}"
        req = requests.get(url, headers=header)
        datas = req.json()
        if not datas:
            break
        data_list.extend([date_pat.match(i.get(date_key)).group() for i in datas])

    date_dic = {}

    start_date = min(data_list)
    end_date = date.today()
    for date_str in data_list:
        if not date_dic.get(date_str):
            date_dic[date_str] = 0
        date_dic[date_str] += 1

    date_list = pd.date_range(start_date, end_date)
    star_num = 0
    star_num_list = []
    for date_str in date_list:
        star_num += date_dic.get(str(date_str).split()[0], 0)
        star_num_list.append(star_num)
    data = {
        "name": name,
        "num_list": star_num_list,
        "date_list": date_list,
    }
    return data

def create_svg(project, datas, save_path, theme=""):

    fig, ax = plt.subplots(figsize=(12, 5))

    # 设置透明
    fig.patch.set_alpha(.0)
    ax.patch.set_alpha(.0)

    # 坐标
    ax.tick_params(color='darkgrey', labelcolor='darkgrey')

    # 坐标轴
    plt.gca().yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.spines['top'].set_color('none')
    ax.spines['bottom'].set_color("darkgrey")
    ax.spines['left'].set_color("darkgrey")
    ax.spines['right'].set_color('none')

    # 绘线
    for data in datas:
        date_list = data["date_list"]
        num_list = data["num_list"]
        name = data["name"]
        ax.plot(date_list, num_list, label=name)

    # 图例
    ax.legend(
        frameon=False,
        loc=2,
        bbox_to_anchor=(1.05, 0.0, 3.0, 0.0),
        borderaxespad = 0.,
        labelcolor='darkgrey'
    )

    # 标题
    ax.set_title(f"{project} history", color='darkgrey')

    # 网格
    ax.grid(True, linestyle='-.')

    plt.savefig(save_path)


def main(user, project, save_path, theme="", token=""):
    datas = [
        get_project_info(user, project, "star", "stargazers", "starred_at",token),
        get_project_info(user, project, "fork", "forks", "created_at",token)
    ]
    create_svg(project, datas, save_path, theme)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='manual to this script')
    parser.add_argument("--user", type=str)
    parser.add_argument("--project", type=str)
    parser.add_argument("--save_path", type=str)
    parser.add_argument("--theme", type=str, default="")
    parser.add_argument("--token", type=str, default="")
    args = parser.parse_args()
    main(args.user, args.project, args.save_path, args.theme, args.token)
