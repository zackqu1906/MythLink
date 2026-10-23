"""为后续在线学习冻结命名特征；当前不训练 LinUCB，也不维护 A/b 矩阵。

保存稀疏命名特征及 schema，避免回收反馈时用已经变化的场景重新编码。
未来训练器需固定类别词表/编码版本。这里不承诺确定性日志可作无偏离线评估。
"""
import math

FEATURE_SCHEMA = 'scene-statistics-features-1'


def feature_snapshot(context, item):
    hour = context['local_hour']
    features = dict(bias=1.0, global_prior=item['components']['global_prior'],
                    context_estimate=item['score'], parameters_ready=float(not item['missing_parameters']),
                    log_usage_mass=math.log1p(item['decayed_usage']),
                    app_missing=float(context['app'] is None), hour_missing=float(hour is None),
                    weekday_missing=float(context['weekday'] is None))
    if hour is not None:
        features.update(hour_sin=math.sin(2 * math.pi * hour / 24), hour_cos=math.cos(2 * math.pi * hour / 24))
    if context['app']:
        features['app:' + context['app']] = 1.0
    if context['weekday'] is not None:
        features['weekday:' + str(context['weekday'])] = 1.0
    return features
