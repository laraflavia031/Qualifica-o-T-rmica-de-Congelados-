# -*- coding: utf-8 -*-
"""
Módulo auxiliar para cálculos de maturação de gelo em freezers e containers
"""

import pandas as pd
import numpy as np
from datetime import timedelta

def calcular_tempo_maturacao(df, sensores_dict, sensor_externo, limite_min, limite_max):
    """
    Calcula o tempo de maturação (tempo até todos os sensores entrarem no critério).
    
    Args:
        df: DataFrame com dados de temperatura
        sensores_dict: Dicionário {nome_sensor: coluna_df}
        sensor_externo: Nome do sensor externo (para excluir do cálculo)
        limite_min: Limite mínimo de temperatura
        limite_max: Limite máximo de temperatura
    
    Returns:
        dict com:
            - 'tempo_maturacao_horas': Tempo em horas (float)
            - 'tempo_maturacao_dias': String formatada 'X dias Y horas Z min'
            - 'data_hora_maturacao': Timestamp quando atingiu maturação
            - 'passou': True se completou maturação
    """
    
    # Obter sensores internos (excluindo sensor externo)
    sensores_internos = {s: col for s, col in sensores_dict.items() if s != sensor_externo}
    
    if not sensores_internos:
        return {
            'tempo_maturacao_horas': 0,
            'tempo_maturacao_dias': '0h',
            'data_hora_maturacao': None,
            'passou': False,
            'motivo': 'Nenhum sensor interno encontrado'
        }
    
    # Verificar se todos os sensores estão dentro do critério em cada linha
    df_verificacao = df.copy()
    
    # Para cada sensor, verificar se está dentro do critério
    for sensor_nome, coluna in sensores_internos.items():
        df_verificacao[f'{sensor_nome}_ok'] = (
            (df_verificacao[coluna] >= limite_min) & 
            (df_verificacao[coluna] <= limite_max)
        )
    
    # Colunas de verificação
    colunas_ok = [f'{s}_ok' for s in sensores_internos.keys()]
    
    # Encontrar a primeira linha onde TODOS os sensores estão ok
    df_verificacao['todos_ok'] = df_verificacao[colunas_ok].all(axis=1)
    
    # Encontrar índice da primeira linha onde todos estão ok
    indices_ok = df_verificacao[df_verificacao['todos_ok']].index
    
    if len(indices_ok) == 0:
        return {
            'tempo_maturacao_horas': 0,
            'tempo_maturacao_dias': '0h',
            'data_hora_maturacao': None,
            'passou': False,
            'motivo': 'Nenhum momento onde todos os sensores entraram no critério'
        }
    
    # Primeira linha onde todos entraram no critério
    idx_maturacao = indices_ok[0]
    data_hora_maturacao = df.loc[idx_maturacao, 'timestamp']
    
    # Tempo desde o início até maturação
    tempo_maturacao = data_hora_maturacao - df['timestamp'].min()
    tempo_maturacao_horas = tempo_maturacao.total_seconds() / 3600
    
    # Formatar tempo em dias, horas e minutos
    dias = int(tempo_maturacao.days)
    segundos_restantes = tempo_maturacao.seconds
    horas = int(segundos_restantes // 3600)
    minutos = int((segundos_restantes % 3600) // 60)
    
    if dias > 0:
        tempo_formatado = f'{dias} dias, {horas} horas e {minutos} min'
    elif horas > 0:
        tempo_formatado = f'{horas} horas e {minutos} min'
    else:
        tempo_formatado = f'{minutos} min'
    
    return {
        'tempo_maturacao_horas': tempo_maturacao_horas,
        'tempo_maturacao_dias': tempo_formatado,
        'data_hora_maturacao': data_hora_maturacao,
        'passou': True,
        'motivo': 'Maturação completada'
    }


def calcular_estabilizacao(df, sensores_dict, sensor_externo, limite_min, limite_max, 
                          data_hora_inicio_estabilizacao, duracao_estabilizacao_horas=24):
    """
    Verifica se durante o período de estabilização (24h após maturação),
    todos os sensores mantêm o critério.
    
    Args:
        df: DataFrame com dados de temperatura
        sensores_dict: Dicionário {nome_sensor: coluna_df}
        sensor_externo: Nome do sensor externo
        limite_min: Limite mínimo
        limite_max: Limite máximo
        data_hora_inicio_estabilizacao: Timestamp de início da estabilização
        duracao_estabilizacao_horas: Duração da estabilização (padrão 24h)
    
    Returns:
        dict com:
            - 'passou': True se manteve critério durante estabilização
            - 'data_hora_fim_estabilizacao': Timestamp de fim
            - 'tempo_fora_criterio': Tempo total fora do critério (em segundos)
            - 'sensores_com_desvio': Lista de sensores que saíram do critério
    """
    
    sensores_internos = {s: col for s, col in sensores_dict.items() if s != sensor_externo}
    
    if not sensores_internos:
        return {
            'passou': False,
            'data_hora_fim_estabilizacao': None,
            'tempo_fora_criterio': 0,
            'sensores_com_desvio': [],
            'motivo': 'Nenhum sensor interno'
        }
    
    # Período de estabilização
    data_hora_fim_estabilizacao = data_hora_inicio_estabilizacao + timedelta(hours=duracao_estabilizacao_horas)
    
    # Filtrar dados do período de estabilização
    df_estabilizacao = df[
        (df['timestamp'] >= data_hora_inicio_estabilizacao) &
        (df['timestamp'] <= data_hora_fim_estabilizacao)
    ].copy()
    
    if len(df_estabilizacao) == 0:
        return {
            'passou': False,
            'data_hora_fim_estabilizacao': data_hora_fim_estabilizacao,
            'tempo_fora_criterio': 0,
            'sensores_com_desvio': [],
            'motivo': 'Nenhum dado no período de estabilização'
        }
    
    # Verificar cada sensor
    sensores_com_desvio = []
    tempo_total_fora = 0
    
    for sensor_nome, coluna in sensores_internos.items():
        # Dados do sensor durante estabilização
        dados_sensor = df_estabilizacao[coluna].dropna()
        
        # Verificar se algum valor saiu do critério
        fora_criterio = (dados_sensor < limite_min) | (dados_sensor > limite_max)
        
        if fora_criterio.any():
            sensores_com_desvio.append(sensor_nome)
            # Contar tempo fora do critério
            tempo_fora = fora_criterio.sum()  # Número de registros fora
            tempo_total_fora += tempo_fora
    
    passou = len(sensores_com_desvio) == 0
    
    return {
        'passou': passou,
        'data_hora_fim_estabilizacao': data_hora_fim_estabilizacao,
        'tempo_fora_criterio': tempo_total_fora,
        'sensores_com_desvio': sensores_com_desvio,
        'motivo': 'Estabilização mantida' if passou else f'Desvio em: {", ".join(sensores_com_desvio)}'
    }


def formatar_resultado_maturacao(resultado_maturacao, resultado_estabilizacao=None):
    """
    Formata os resultados de maturação e estabilização para exibição.
    
    Args:
        resultado_maturacao: Dict retornado por calcular_tempo_maturacao()
        resultado_estabilizacao: Dict retornado por calcular_estabilizacao() (opcional)
    
    Returns:
        String formatada para exibição no relatório
    """
    
    linhas = []
    
    if resultado_maturacao['passou']:
        linhas.append(f"<b>Tempo de Maturação:</b> {resultado_maturacao['tempo_maturacao_dias']}")
        linhas.append(f"<b>Atingido em:</b> {resultado_maturacao['data_hora_maturacao'].strftime('%d/%m/%Y %H:%M')}")
    else:
        linhas.append(f"<b>Status Maturação:</b> NÃO ATINGIDA")
        linhas.append(f"<b>Motivo:</b> {resultado_maturacao['motivo']}")
    
    if resultado_estabilizacao:
        if resultado_estabilizacao['passou']:
            linhas.append(f"<b>Estabilização:</b> MANTIDA (24 horas)")
        else:
            linhas.append(f"<b>Estabilização:</b> NÃO MANTIDA")
            linhas.append(f"<b>Sensores com desvio:</b> {', '.join(resultado_estabilizacao['sensores_com_desvio'])}")
    
    return '<br/>'.join(linhas)
