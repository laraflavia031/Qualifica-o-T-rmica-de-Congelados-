#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gerador de Relatórios de Qualificação Térmica

"""

SCRIPT_VERSION = "1.0.0"

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, 
                               PageBreak, Image, Flowable, KeepTogether)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.pdfgen import canvas
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from io import BytesIO
import os
from pathlib import Path
import sys
import hashlib
import unicodedata
import re

# ============================================================================
# FUNCOES DE MATURACAO (Integradas)
# ============================================================================

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


# ============================================================================

def normalizar_texto(texto):
    """Normaliza o texto removendo acentos, espaços extras e convertendo para minúsculas"""
    texto = str(texto).strip().lower()
    texto = unicodedata.normalize('NFD', texto)
    texto = texto.encode('ascii', 'ignore').decode('utf-8')
    texto = re.sub(r'\s+', ' ', texto)
    return texto

print("=" * 80)
print("  GERADOR DE RELATÓRIOS DE QUALIFICAÇÃO TÉRMICA")
print(f"  Versão: {SCRIPT_VERSION}")
print("  Suporta: 1-10 estudos com DATA/HORA separados, Temperatura e Umidade")
print("=" * 80)
print("\nBibliotecas carregadas com sucesso!")
# Script pronto para uso

# ============================================================================
# CLASSE PARA TAGS ROTACIONADOS (90°)
# ============================================================================

class RotatedText(Flowable):
    """Desenha texto rotacionado em 90° para caber em colunas estreitas"""
    def __init__(self, text, font_size=6):
        Flowable.__init__(self)
        self.text = str(text)
        self.font_size = font_size
        self.width = 5*mm
        self.height = 20*mm
    
    def draw(self):
        self.canv.saveState()
        # Usar fonte que suporta caracteres especiais (Helvetica padrão não suporta bem acentos em algumas configurações)
        # Vamos garantir que o texto seja decodificado corretamente
        texto_limpo = self.text.replace('\n', ' ')
        self.canv.setFont("Helvetica-Bold", self.font_size)
        self.canv.rotate(90)
        self.canv.drawCentredString(10*mm, -2.5*mm, texto_limpo)
        self.canv.restoreState()

# ============================================================================
# FUNÇÕES DE FORMATAÇÃO
# ============================================================================

def formatar_data_br(data_obj):
    """Formata data para DD/MM/AAAA"""
    if isinstance(data_obj, str):
        try:
            data_obj = pd.to_datetime(data_obj, dayfirst=True)
        except:
            return str(data_obj)
    
    if pd.isna(data_obj):
        return ""
    
    return data_obj.strftime('%d/%m/%Y')

def formatar_hora_br(hora_obj):
    """Formata hora para 00:00"""
    if isinstance(hora_obj, str):
        try:
            hora_obj = pd.to_datetime(hora_obj, format='%H:%M:%S').time()
        except:
            try:
                hora_obj = pd.to_datetime(hora_obj, format='%H:%M').time()
            except:
                return "00:00"
    
    if pd.isna(hora_obj):
        return "00:00"
    
    if hasattr(hora_obj, 'strftime'):
        return hora_obj.strftime('%H:%M')
    
    return "00:00"

def formatar_duracao_br(horas, minutos=0):
    """Formata duração no formato 'XXh e YY min' ou apenas 'XXh' se minutos=0"""
    horas_int = int(horas)
    minutos_int = int(minutos)
    
    if minutos_int == 0:
        return f"{horas_int}h"
    else:
        return f"{horas_int}h e {minutos_int} min"

def adicionar_logo_unilog(elements, styles, posicao='direita'):
    """Adiciona logo da Unilog no canto superior direito (placeholder para implementação)"""
    # Esta função será chamada para adicionar a logo em cada página de anexo
    # Por enquanto, é um placeholder que pode ser expandido com a logo real
    pass

def calcular_larguras_colunas(num_sensores, tem_sensor_externo=False, largura_util=281*mm):
    """Calcula larguras responsivas das colunas"""
    col_data = 12 * mm
    col_hora = 10 * mm
    col_calc = 5 * mm
    
    espaco_fixo = col_data + col_hora + (col_calc * 3)
    espaco_sensores = largura_util - espaco_fixo
    
    total_sensores = num_sensores + (1 if tem_sensor_externo else 0)
    largura_sensor = espaco_sensores / total_sensores
    
    colWidths = [col_data, col_hora]
    colWidths.extend([largura_sensor] * total_sensores)
    colWidths.extend([col_calc, col_calc, col_calc])
    
    return colWidths


def calcular_larguras_resumo(num_sensores, tem_sensor_externo=False, largura_util=281*mm):
    """Calcula larguras responsivas para a tabela de resumo (sem DATA e HORA)"""
    col_estatistica = 20 * mm
    col_calc = 5 * mm
    
    espaco_fixo = col_estatistica + (col_calc * 3)
    espaco_sensores = largura_util - espaco_fixo
    
    total_sensores = num_sensores + (1 if tem_sensor_externo else 0)
    largura_sensor = espaco_sensores / total_sensores
    
    colWidths = [col_estatistica]
    colWidths.extend([largura_sensor] * total_sensores)
    colWidths.extend([col_calc, col_calc, col_calc])
    
    return colWidths


def calcular_fonte_responsiva(largura_coluna_mm, min_font=5, max_font=12):
    """
    Calcula o tamanho da fonte de forma responsiva baseado na largura da coluna.
    
    Args:
        largura_coluna_mm: Largura da coluna em milímetros
        min_font: Tamanho mínimo de fonte em pontos
        max_font: Tamanho máximo de fonte em pontos
    
    Returns:
        Tamanho da fonte em pontos
    """
    # Conversão: 1mm ≈ 2.834645669 pontos
    # Proporção: aproximadamente 1 ponto de fonte por 0.35mm de largura
    
    if largura_coluna_mm < 5:
        return min_font
    elif largura_coluna_mm > 50:
        return max_font
    else:
        # Escala linear entre min e max
        fonte = min_font + (largura_coluna_mm - 5) * (max_font - min_font) / (50 - 5)
        return max(min_font, min(max_font, fonte))

def gerar_estilo_tabela_dados():
    """Est ilo da tabela de dados brutos com centralização completa"""
    estilo = [
        ('GRID', (0, 0), (-1, -1), 0.3, colors.black),
        ('FONTSIZE', (0, 0), (-1, 0), 6),
        ('FONTSIZE', (0, 1), (-1, -1), 5),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),  # Reduzido para descer um pouco o valor na célula
    ]
    return estilo

def ordenar_sensores_numericamente(sensores_dict, sensor_externo=None):
    """Ordena sensores de forma sequencial numérica em ordem crescente, deixando sensor externo por último"""
    import re
    
    def extrair_numero(nome_sensor):
        """Extrai o número do nome do sensor para ordenação crescente"""
        # Extrair todos os números e pegar o maior (para casos como G50, G49, etc.)
        numeros = re.findall(r'\d+', nome_sensor)
        if numeros:
            return int(numeros[-1])  # Pega o último número encontrado
        return float('inf')
    
    # Separar sensores internos do sensor externo
    sensores_internos = {k: v for k, v in sensores_dict.items() if k != sensor_externo}
    
    # Ordenar sensores internos em ordem crescente pelo número extraído (reverse=False garante crescente)
    sensores_ordenados = dict(sorted(sensores_internos.items(), key=lambda x: extrair_numero(x[0]), reverse=False))
    
    # Adicionar sensor externo por último (se existir)
    if sensor_externo and sensor_externo in sensores_dict:
        sensores_ordenados[sensor_externo] = sensores_dict[sensor_externo]
    
    return sensores_ordenados

def gerar_estilo_tabela_resumo(num_sensores, idx_max_por_sensor, idx_min_por_sensor, idx_sensor_externo=None):
    """Estilo da tabela de resumo com destaques e responsividade melhorada"""
    estilo = [
        ('GRID', (0, 0), (-1, -1), 0.3, colors.black),
        ('FONTSIZE', (0, 0), (-1, 0), 5),
        ('FONTSIZE', (0, 1), (-1, -1), 4),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (-3, 0), (-1, -1), colors.HexColor('#ADD8E6')),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]
    
    # Destacar sensor externo em cinza
    if idx_sensor_externo is not None:
        estilo.append(('BACKGROUND', (idx_sensor_externo, 1), (idx_sensor_externo, -1), 
                     colors.HexColor('#D3D3D3')))
    
    if idx_max_por_sensor:
        for col_idx in idx_max_por_sensor:
            estilo.append(('BACKGROUND', (col_idx, 1), (col_idx, 1), 
                         colors.HexColor('#FF6B6B')))
    
    if idx_min_por_sensor:
        for col_idx in idx_min_por_sensor:
            estilo.append(('BACKGROUND', (col_idx, 2), (col_idx, 2), 
                         colors.HexColor('#ADD8E6')))
    
    return estilo

# ============================================================================
# FUNÇÕES DE AUDITORIA
# ============================================================================

def criar_log_auditoria():
    """Cria arquivo de log de auditoria"""
    log_path = Path.home() / "Downloads" / "audit_log.txt"
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("=" * 80 + "\n")
        f.write(f"LOG DE AUDITORIA - Gerador GxP Unificado v{SCRIPT_VERSION}\n")
        f.write("=" * 80 + "\n\n")
    return log_path

def log_auditoria(mensagem, nivel="INFO", log_path=None):
    """Adiciona entrada no log"""
    if log_path is None:
        log_path = Path.home() / "Downloads" / "audit_log.txt"
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    linha = f"[{timestamp}] [{nivel}] {mensagem}\n"
    
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(linha)
    
    if nivel == "ERROR":
        print(f"{mensagem}")
    elif nivel == "WARNING":
        print(f"{mensagem}")
    else:
        print(f"{mensagem}")

def calcular_hash_arquivo(caminho):
    """Calcula hash SHA-256"""
    sha256 = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(4096), b""):
            sha256.update(bloco)
    return sha256.hexdigest()

# ============================================================================
# CLASSE PRINCIPAL
# ============================================================================

class GeradorRelatorioGxP:
    """Gerador unificado de relatórios GxP com suporte a múltiplos arquivos"""
    
    def __init__(self):
        self.arquivos = []  # Lista de arquivos carregados
        self.estudos = []   # Lista de estudos com arquivo_idx associado
        self.log_path = criar_log_auditoria()
        self.empresa = ""
        self.area = ""
        self.tag = ""
        self.limite_min_temp = 0
        self.limite_max_temp = 0
        self.tratar_umidade = False
        self.limite_min_umidade = 0
        self.limite_max_umidade = 0
        # Sensores por arquivo, não globais
        self.sensores_por_arquivo = {}  # {arquivo_idx: {'temp': {}, 'umidade': {}}}
        self.dados_por_arquivo = {}     # {arquivo_idx: df}
        # Sensor externo por arquivo
        self.sensor_externo_por_arquivo = {}  # {arquivo_idx: sensor_name}
        # Colunas de data e hora por arquivo
        self.col_data_por_arquivo = {}  # {arquivo_idx: col_data}
        self.col_hora_por_arquivo = {}  # {arquivo_idx: col_hora}
        self.estudos_info = []  # Lista com informações de cada estudo (nome, data, hora, duração)
        self.total_paginas = 0  # Total de páginas do PDF
        self.paginas_estudos = {}  # {estudo_idx: (pagina_inicio, pagina_fim)} para rastrear páginas de cada estudo
        self.tipo_equipamento = ""  # "refrigerador", "galpao", "freezer" ou "container"
        self.sensores_doca_por_estudo = {}  # {estudo_idx: [sensores_doca]} ou {1: [sensores_doca]} se 1 arquivo
        # Novos atributos para freezer/container
        self.modo_equipamento = "conservacao"  # "conservacao" ou "maturacao" (para freezer/container)
        self.incluir_teste_maturacao = False  # Se deve incluir teste automatico de maturacao
        self.dados_maturacao = {}  # {estudo_idx: {'tempo_maturacao_horas': X, 'tempo_maturacao_dias': 'X dias Y horas Z min', 'passou_estabilizacao': True/False}}
        
        log_auditoria("Sistema iniciado", "INFO", self.log_path)
    
    def selecionar_arquivos(self):
        """Seleciona múltiplos arquivos Excel (até 10)"""
        print("\n" + "=" * 80)
        print("  SELEÇÃO DE ARQUIVOS")
        print("=" * 80)
        print("\nDigite os caminhos dos arquivos Excel (máximo 10)")
        print("Deixe em branco para terminar\n")
        
        contador = 0
        while contador < 10:
            arquivo = input(f"Arquivo {contador + 1}: ").strip()
            
            if not arquivo:
                if contador == 0:
                    print("Deve informar pelo menos 1 arquivo!")
                    continue
                break
            
            arquivo = arquivo.strip('"').strip("'")
            
            if not os.path.exists(arquivo):
                log_auditoria(f"Arquivo não encontrado: {arquivo}", "ERROR", self.log_path)
                print(f"Erro: Arquivo não encontrado: {arquivo}")
                continue
            
            if not arquivo.lower().endswith(('.xlsx', '.xls', '.csv')):
                log_auditoria(f"Formato inválido: {arquivo}", "ERROR", self.log_path)
                print("Arquivo deve ser Excel (.xlsx, .xls) ou CSV (.csv)")
                continue
            
            hash_arquivo = calcular_hash_arquivo(arquivo)
            self.arquivos.append({
                'caminho': arquivo,
                'hash': hash_arquivo,
                'nome': os.path.basename(arquivo)
            })
            
            log_auditoria(f"Arquivo {contador + 1} selecionado: {os.path.basename(arquivo)}", 
                         "INFO", self.log_path)
            log_auditoria(f"Hash SHA-256: {hash_arquivo}", "INFO", self.log_path)
            
            print(f"✅ Arquivo adicionado: {os.path.basename(arquivo)}")
            print(f"🔐 Hash: {hash_arquivo[:16]}...")
            
            contador += 1
        
        if not self.arquivos:
            print("Nenhum arquivo válido selecionado!")
            return False
        
        print(f"\n✅ {len(self.arquivos)} arquivo(s) selecionado(s)")
        return True
    
    def carregar_dados_arquivo(self, arquivo_info):
        """Carrega dados de um arquivo Excel"""
        caminho = arquivo_info['caminho']
        nome_arquivo = arquivo_info['nome']
        
        print(f"\nCarregando arquivo: {nome_arquivo}...")
        
        try:
            if caminho.lower().endswith('.csv'):
                df = pd.read_csv(caminho)
            else:
                df = pd.read_excel(caminho)
            
            log_auditoria(f"Arquivo carregado: {len(df)} registros", "INFO", self.log_path)
            print(f"✅ Arquivo carregado: {len(df)} registros")
            
            return df
            
        except Exception as e:
            log_auditoria(f"Erro ao carregar {nome_arquivo}: {e}", "ERROR", self.log_path)
            print(f"Erro: {e}")
            return None
    
    def processar_arquivo(self, df, arquivo_info, num_estudo):
        """Processa um arquivo e extrai informações de sensores"""
        print(f"\n{'='*80}")
        print(f"  PROCESSANDO ARQUIVO {num_estudo}: {arquivo_info['nome']}")
        print(f"{'='*80}")
        
        # Identificar colunas DATA e HORA (case-insensitive, português e inglês)
        col_data = None
        col_hora = None
        
        for col in df.columns:
            col_lower = str(col).lower().strip()
            # Procurar por DATA (português) ou DATE (inglês)
            if (col_lower == 'data' or col_lower == 'date' or 
                col_lower.startswith('data') or col_lower.startswith('date')) and col_data is None:
                col_data = col
            # Procurar por HORA (português) ou TIME (inglês)
            if (col_lower == 'hora' or col_lower == 'time' or 
                col_lower.startswith('hora') or col_lower.startswith('time')) and col_hora is None:
                col_hora = col
        
        if not col_data or not col_hora:
            log_auditoria(f"Colunas DATA ou HORA não encontradas em {arquivo_info['nome']}", 
                         "ERROR", self.log_path)
            print("Colunas DATA e HORA não encontradas!")
            print(f"   Colunas disponíveis: {list(df.columns[:10])}...")
            return None
        
        # Validar que DATA e HORA têm dados
        if df[col_data].isna().all() or df[col_hora].isna().all():
            log_auditoria(f"DATA ou HORA vazias em {arquivo_info['nome']}", 
                         "ERROR", self.log_path)
            print("Colunas DATA ou HORA estão vazias!")
            return None
        
        # Limpar dados inválidos de DATA e HORA
        df[col_data] = df[col_data].replace(['NaT', 'nan', 'NaN', 'null', 'NULL', ''], pd.NA)
        df[col_hora] = df[col_hora].replace(['NaT', 'nan', 'NaN', 'null', 'NULL', ''], pd.NA)
        
        # Remover linhas onde DATA ou HORA estão vazias
        df_original_len = len(df)
        df = df.dropna(subset=[col_data, col_hora])
        
        if len(df) == 0:
            log_auditoria(f"Nenhum dado válido de DATA/HORA em {arquivo_info['nome']}", 
                         "ERROR", self.log_path)
            print("Nenhum registro com DATA/HORA válida encontrado!")
            return None
        
        if len(df) < df_original_len:
            linhas_removidas = df_original_len - len(df)
            log_auditoria(f"{linhas_removidas} linhas removidas por DATA/HORA inválida", 
                         "WARNING", self.log_path)
            print(f"{linhas_removidas} linhas removidas por DATA/HORA inválida")
        
        # Criar coluna timestamp
        try:
            # Converter para string e remover espaços extras
            data_str = df[col_data].astype(str).str.strip()
            hora_str = df[col_hora].astype(str).str.strip()
            
            # Tentar formato brasileiro DD/MM/YYYY primeiro
            df['timestamp'] = pd.to_datetime(
            data_str + ' ' + hora_str,
            format='%d/%m/%Y %H:%M:%S',
            errors='coerce'
            )
            
            # Se falhar, tentar formato ISO YYYY-MM-DD
            linhas_falhadas = df['timestamp'].isna()
            if linhas_falhadas.any():
                df.loc[linhas_falhadas, 'timestamp'] = pd.to_datetime(
                    data_str[linhas_falhadas] + ' ' + hora_str[linhas_falhadas],
                    format='%Y-%m-%d %H:%M:%S',
                    errors='coerce'
                )
            
            # Verificar se houve conversões falhadas
            linhas_com_erro = df['timestamp'].isna().sum()
            if linhas_com_erro > 0:
                log_auditoria(f"{linhas_com_erro} linhas com DATA/HORA inválida", 
                             "WARNING", self.log_path)
                print(f"{linhas_com_erro} linhas com DATA/HORA inválida")
                # Remover linhas com timestamp inválido
                df = df.dropna(subset=['timestamp'])
                
                if len(df) == 0:
                    log_auditoria(f"Nenhum dado válido após limpeza de DATA/HORA", 
                                 "ERROR", self.log_path)
                    print("Nenhum registro com DATA/HORA válida após limpeza!")
                    return None
        
        except Exception as e:
            log_auditoria(f"Erro ao processar DATA/HORA em {arquivo_info['nome']}: {e}", 
                         "ERROR", self.log_path)
            print(f"Erro ao processar DATA/HORA: {e}")
            return None
        
        df = df.sort_values('timestamp').reset_index(drop=True)
        
        # Identificar sensores de temperatura e umidade
        sensores_temp = {}
        sensores_umidade = {}
        
        for col in df.columns:
            if col not in [col_data, col_hora, 'timestamp']:
                col_lower = col.lower()
                
                # Verificar se é temperatura (com ou sem colchetes)
                if any(x in col for x in ['[°C]', '[ºC]', '[°c]', '[ºc]', '[C]', '[c]', '°C', 'ºC']):
                    nome_base = col
                    for remover in ['[°C]', '[ºC]', '[°c]', '[ºc]', '[C]', '[c]', '°C', 'ºC', ' [°C]', ' °C']:
                        nome_base = nome_base.replace(remover, '').strip()
                    if nome_base:
                        sensores_temp[nome_base] = col
                
                # Verificar se é umidade (com ou sem colchetes)
                elif any(x in col for x in ['[%Hr]', '[%rH]', '[%RH]', '[%hr]', '%Hr', '%rH', '%RH', '%hr']):
                    nome_base = col
                    for remover in ['[%Hr]', '[%rH]', '[%RH]', '[%hr]', '%Hr', '%rH', '%RH', '%hr']:
                        nome_base = nome_base.replace(remover, '').strip()
                    if nome_base:
                        sensores_umidade[nome_base] = col
        
        if not sensores_temp:
            log_auditoria(f"Nenhum sensor de temperatura encontrado automaticamente em {arquivo_info['nome']}", 
                         "WARNING", self.log_path)
            print("\nNenhum sensor de temperatura identificado automaticamente!")
            print("   Procurou por: [°C], °C, [ºC], ºC, [C], C, etc.")
            print("\nColunas disponíveis no arquivo:")
            
            # Listar colunas disponíveis (excluindo DATA e HORA)
            colunas_disponiveis = [col for col in df.columns if col not in [col_data, col_hora, 'timestamp']]
            for idx, col in enumerate(colunas_disponiveis, 1):
                print(f"   {idx}. {col}")
            
            # Perguntar ao usuário quais são os sensores de temperatura
            print("\nIDENTIFICAÇÃO MANUAL DE SENSORES:")
            print("   Digite os números das colunas que são sensores de temperatura")
            print("   (separados por vírgula, ex: 1,2,3,4,5)")
            
            escolha = input("   Sensores de temperatura: ").strip()
            
            if not escolha:
                log_auditoria(f"Nenhum sensor de temperatura selecionado em {arquivo_info['nome']}", 
                             "ERROR", self.log_path)
                print("Nenhum sensor selecionado!")
                return None
            
            try:
                indices = [int(x.strip()) - 1 for x in escolha.split(',')]
                for idx in indices:
                    if 0 <= idx < len(colunas_disponiveis):
                        col = colunas_disponiveis[idx]
                        sensores_temp[col] = col
                    else:
                        print(f"Índice {idx + 1} inválido")
                
                if not sensores_temp:
                    log_auditoria(f"Nenhum sensor válido selecionado em {arquivo_info['nome']}", 
                                 "ERROR", self.log_path)
                    print("Nenhum sensor válido foi selecionado!")
                    return None
                
                print(f"\n✅ {len(sensores_temp)} sensor(es) selecionado(s) manualmente:")
                for sensor in sensores_temp.keys():
                    print(f"   - {sensor}")
                    
            except ValueError:
                log_auditoria(f"Erro ao processar seleção de sensores em {arquivo_info['nome']}", 
                             "ERROR", self.log_path)
                print("Erro ao processar seleção. Use números separados por vírgula.")
                return None
        
        # Contar sensores únicos
        sensores_unicos = set(sensores_temp.keys()) | set(sensores_umidade.keys())
        print(f"✅ {len(sensores_unicos)} sensor(es) ÚNICO(s) identificado(s)")
        print(f"   - {len(sensores_temp)} com temperatura [°C]")
        if sensores_umidade:
            print(f"   - {len(sensores_umidade)} com umidade [%Hr]")
        
        print(f"📅 Período: {df['timestamp'].min().strftime('%d/%m/%Y %H:%M')} a "
              f"{df['timestamp'].max().strftime('%d/%m/%Y %H:%M')}")
        
        # Perguntar quantos sensores o teste iniciou
        print(f"\nVALIDAÇÃO DE INTEGRIDADE DE DADOS:")
        num_sensores_esperados = int(input(f"   Com quantos sensores o teste iniciou? "))
        
        # Validar integridade
        integridade_ok = self._validar_integridade(df, sensores_temp, sensores_umidade, 
                                                   num_sensores_esperados, arquivo_info['nome'])
        
        if not integridade_ok:
            return None
        
        # Selecionar sensor externo para ESTE arquivo
        sensor_externo_selecionado = None
        if sensores_temp:
            print(f"\nSELEÇÃO DE SENSOR EXTERNO:")
            print("   Qual sensor será destacado em cinza no relatório?")
            print("   Sensores disponíveis:")
            
            sensores_lista = list(sensores_temp.keys())
            for idx, sensor in enumerate(sensores_lista, 1):
                print(f"   {idx}. {sensor}")
            print(f"   0. Nenhum sensor externo")
            
            escolha = input("\n   Escolha o número do sensor: ").strip()
            
            try:
                escolha_int = int(escolha)
                if 1 <= escolha_int <= len(sensores_lista):
                    sensor_externo_selecionado = sensores_lista[escolha_int - 1]
                    print(f"   ✅ Sensor externo selecionado: {sensor_externo_selecionado}")
                    log_auditoria(f"Arquivo {num_estudo}: Sensor externo selecionado: {sensor_externo_selecionado}", 
                                 "INFO", self.log_path)
                elif escolha_int == 0:
                    sensor_externo_selecionado = ""
                    print(f"   Nenhum sensor externo será destacado")
                else:
                    print(f"   Opção inválida. Nenhum sensor externo selecionado.")
                    sensor_externo_selecionado = ""
            except ValueError:
                print(f"   Opção inválida. Nenhum sensor externo selecionado.")
                sensor_externo_selecionado = ""
        
        return {
            'df': df,
            'sensores_temp': sensores_temp,
            'sensores_umidade': sensores_umidade,
            'col_data': col_data,
            'col_hora': col_hora,
            'arquivo': arquivo_info,
            'sensor_externo': sensor_externo_selecionado
        }
    
    def _validar_integridade(self, df, sensores_temp, sensores_umidade, num_esperados, nome_arquivo):
        """Valida integridade de dados (80% mínimo)"""
        sensores_unicos = set(sensores_temp.keys()) | set(sensores_umidade.keys())
        total_sensores = len(sensores_unicos)
        
        if total_sensores < num_esperados:
            log_auditoria(f"Sensores faltando em {nome_arquivo}: esperado {num_esperados}, "
                         f"encontrado {total_sensores}", "ERROR", self.log_path)
            print(f"Sensores faltando: esperado {num_esperados}, encontrado {total_sensores}")
            return False
        
        # Contar dados válidos por sensor
        dados_validos = 0
        total_dados = 0
        
        for sensor_nome, col_original in sensores_temp.items():
            total_dados += len(df)
            try:
                df[col_original] = pd.to_numeric(df[col_original], errors='coerce')
                validos = df[col_original].notna().sum()
            except:
                validos = df[col_original].notna().sum()
            dados_validos += validos
        
        for sensor_nome, col_original in sensores_umidade.items():
            total_dados += len(df)
            try:
                df[col_original] = pd.to_numeric(df[col_original], errors='coerce')
                validos = df[col_original].notna().sum()
            except:
                validos = df[col_original].notna().sum()
            dados_validos += validos
        
        percentual_valido = (dados_validos / total_dados * 100) if total_dados > 0 else 0
        
        print(f"   Sensores esperados: {num_esperados}")
        print(f"   Sensores encontrados (únicos): {total_sensores}")
        print(f"   Dados válidos: {percentual_valido:.1f}%")
        print(f"   Total de colunas: {len(sensores_temp) + len(sensores_umidade)}")
        
        if percentual_valido < 80:
            log_auditoria(f"Integridade de dados REPROVADA em {nome_arquivo}: {percentual_valido:.1f}% ({total_sensores} sensores)", 
                         "ERROR", self.log_path)
            print(f"\nIntegridade dos dados: REPROVADA")
            print(f"   Disponibilidade: {percentual_valido:.1f}% (mínimo 80%)")
            print(f"   Estudo necessita nova qualificação térmica!")
            return False
        
        log_auditoria(f"Integridade de dados ATENDIDA em {nome_arquivo}: {percentual_valido:.1f}% ({total_sensores} sensores)", 
                     "INFO", self.log_path)
        print(f"\n✅ Integridade dos dados: ATENDIDA")
        print(f"   Disponibilidade: {percentual_valido:.1f}% dos dados válidos")
        print(f"   Sensores únicos: {total_sensores}")
        
        return True
    
    def configurar_parametros_gerais(self):
        """Configura parâmetros gerais"""
        print("\n" + "=" * 80)
        print("  CONFIGURAÇÃO GERAL")
        print("=" * 80)
        
        try:
            self.empresa = input("\nNome da empresa: ").strip()
            self.area = input("Área/Equipamento: ").strip()
            self.tag = input("TAG: ").strip()
            
            # Perguntar sobre tipo de equipamento
            print("\nTIPO DE EQUIPAMENTO/ÁREA:")
            print("   1. Refrigerador ou Câmara Fria")
            print("   2. Galpão")
            opcao_eq = input("   Escolha a opção (1 ou 2): ").strip()
            if opcao_eq == '2':
                self.tipo_equipamento = "galpao"
            elif opcao_eq == '1':
                self.tipo_equipamento = "refrigerador"
            else:
                # Tentar inferir pelo texto digitado na Área/Equipamento se o usuário não escolheu número
                area_norm = normalizar_texto(self.area)
                if any(x in area_norm for x in ["galpao", "g1", "g2", "g3", "g4", "g5", "g6", "g7", "g8", "g9", "g10"]):
                    self.tipo_equipamento = "galpao"
                    print("   Inferido tipo: Galpão")
                else:
                    self.tipo_equipamento = "refrigerador"
                    print("   Inferido tipo: Refrigerador/Câmara Fria")
            
            print("\nCRITÉRIOS DE TEMPERATURA:")
            self.limite_min_temp = float(input("   Temperatura mínima (°C): ").strip())
            self.limite_max_temp = float(input("   Temperatura máxima (°C): ").strip())
            
            # Perguntar sobre umidade
            print("\nTRATAMENTO DE UMIDADE:")
            resposta = input("   Deseja tratar umidade? (S/N): ").strip().upper()
            
            self.tratar_umidade = (resposta == 'S')
            
            if self.tratar_umidade:
                print("\n   CRITÉRIOS DE UMIDADE:")
                self.limite_min_umidade = float(input("   Umidade mínima (%): ").strip())
                self.limite_max_umidade = float(input("   Umidade máxima (%): ").strip())
                print(f"   ✅ Umidade será tratada como estudo separado")
            
            log_auditoria(f"Empresa: {self.empresa}, Área: {self.area}, TAG: {self.tag}", 
                         "INFO", self.log_path)
            log_auditoria(f"Critério Temperatura: {self.limite_min_temp}°C a {self.limite_max_temp}°C", 
                         "INFO", self.log_path)
            if self.tratar_umidade:
                log_auditoria(f"Critério Umidade: {self.limite_min_umidade}% a {self.limite_max_umidade}%", 
                             "INFO", self.log_path)
            
            return True
            
        except Exception as e:
            log_auditoria(f"Erro na configuração: {e}", "ERROR", self.log_path)
            print(f"Erro: {e}")
            return False
    
    def processar_todos_arquivos(self):
        """Processa todos os arquivos selecionados"""
        print("\n" + "=" * 80)
        print("  PROCESSAMENTO DE ARQUIVOS")
        print("=" * 80)
        
        # Primeiro, carregar e processar todos os arquivos
        arquivos_processados = []
        for idx, arquivo_info in enumerate(self.arquivos, 1):
            df = self.carregar_dados_arquivo(arquivo_info)
            if df is None:
                print(f"Erro ao carregar arquivo {idx}")
                return False
            
            dados_processados = self.processar_arquivo(df, arquivo_info, idx)
            if dados_processados is None:
                print(f"Erro ao processar arquivo {idx}")
                return False
            
            # Armazenar sensores por arquivo_idx
            self.sensores_por_arquivo[idx] = {
                'temp': dados_processados['sensores_temp'].copy(),
                'umidade': dados_processados['sensores_umidade'].copy()
            }
            
            # Armazenar dados por arquivo_idx
            self.dados_por_arquivo[idx] = dados_processados['df'].copy()
            
            # Armazenar sensor externo por arquivo_idx
            self.sensor_externo_por_arquivo[idx] = dados_processados['sensor_externo']
            
            # Armazenar colunas de data e hora por arquivo_idx
            self.col_data_por_arquivo[idx] = dados_processados['col_data']
            self.col_hora_por_arquivo[idx] = dados_processados['col_hora']
            
            arquivos_processados.append({
                'arquivo_info': arquivo_info,
                'dados': dados_processados,
                'idx': idx
            })
        
        # Perguntar quantos estudos o usuário quer gerar
        print("\n" + "=" * 80)
        print("  DEFINIÇÃO DE ESTUDOS")
        print("=" * 80)
        print(f"\nVocê carregou {len(self.arquivos)} arquivo(s)")
        print("Quantos estudos você deseja gerar a partir deste(s) arquivo(s)?")
        
        # Extrair apenas o número da entrada (remover texto como "168h com carga")
        entrada_estudos = input("Número de estudos: ").strip()
        # Extrair apenas dígitos da entrada
        import re
        numeros = re.findall(r'\d+', entrada_estudos)
        if numeros:
            num_estudos = int(numeros[0])
        else:
            print("Entrada inválida. Por favor, digite um número.")
            return False
        
        if num_estudos <= 0 or num_estudos > 10:
            print(f"Número de estudos inválido. Use um valor entre 1 e 10.")
            return False
        
        # Coletar informações de cada estudo
        self.estudos_info = []
        for estudo_idx in range(1, num_estudos + 1):
            print(f"\n📅 INFORMAÇÕES DO ESTUDO {estudo_idx}:")
            nome_estudo = input(f"   Nome do estudo: ").strip()
            data_inicio = input(f"   Data de início (DD/MM/YYYY): ").strip()
            hora_inicio = input(f"   Hora de início (HH:MM): ").strip()
            
            # Pergunta sobre teste informativo
            print(f"\n   CLASSIFICAÇÃO DO TESTE:")
            resposta_informativo = input(f"   Este é um teste informativo? (S/N): ").strip().upper()
            teste_informativo = (resposta_informativo == 'S')
            
            # Pergunta sobre unidade de tempo (horas ou minutos)
            print(f"\n   DURAÇÃO DO ESTUDO:")
            unidade_tempo = input(f"   Deseja informar a duração em horas ou minutos? (H/M): ").strip().upper()
            
            if unidade_tempo == 'M':
                entrada_duracao = input(f"   Duração em minutos: ").strip()
                # Extrair números e casas decimais
                numeros = re.findall(r'\d+\.?\d*', entrada_duracao)
                if numeros:
                    duracao_minutos = float(numeros[0])
                    duracao_horas = duracao_minutos / 60
                else:
                    print("   Entrada inválida. Por favor, digite um número.")
                    return False
            else:
                entrada_duracao = input(f"   Duração em horas: ").strip()
                # Extrair números e casas decimais
                numeros = re.findall(r'\d+\.?\d*', entrada_duracao)
                if numeros:
                    duracao_horas = float(numeros[0])
                    duracao_minutos = duracao_horas * 60
                else:
                    print("   Entrada inválida. Por favor, digite um número.")
                    return False
            
            self.estudos_info.append({
                'nome': nome_estudo if nome_estudo else f"ESTUDO {estudo_idx}",
                'data_inicio': data_inicio,
                'hora_inicio': hora_inicio,
                'duracao_horas': duracao_horas,
                'duracao_minutos': duracao_minutos,
                'unidade_tempo': unidade_tempo,
                'teste_informativo': teste_informativo
            })
        
        # Perguntar se quer gerar estudos de umidade (apenas se houver dados de umidade)
        gerar_umidade = False
        tem_umidade_em_algum_arquivo = any(self.sensores_por_arquivo[idx]['umidade'] 
                                          for idx in self.sensores_por_arquivo.keys())
        
        if tem_umidade_em_algum_arquivo:
            print("\n" + "=" * 80)
            print("TRATAMENTO DE UMIDADE")
            print("=" * 80)
            resposta_umidade = input("Deseja gerar também estudos de umidade? (S/N): ").strip().upper()
            gerar_umidade = (resposta_umidade == 'S')
        else:
            print("\n" + "=" * 80)
            print("TRATAMENTO DE UMIDADE")
            print("=" * 80)
            print("Nenhum dado de umidade encontrado nos arquivo(s).")
            print("   Apenas estudos de temperatura serão gerados.")
        
        # Buscar automaticamente o arquivo para cada estudo
        print("\n" + "=" * 80)
        print("BUSCA AUTOMÁTICA DE ARQUIVOS PARA ESTUDOS")
        print("=" * 80)
        
        for estudo_info in self.estudos_info:
            print(f"\n📅 Estudo: {estudo_info['nome']}")
            print(f"   Período: {estudo_info['data_inicio']} {estudo_info['hora_inicio']} "
                  f"({estudo_info['duracao_horas']}h)")
            
            # Tentar converter data/hora do estudo
            try:
                data_inicio = pd.to_datetime(f"{estudo_info['data_inicio']} {estudo_info['hora_inicio']}", 
                                            format='%d/%m/%Y %H:%M', dayfirst=True)
                data_fim = data_inicio + timedelta(hours=estudo_info['duracao_horas'])
            except Exception as e:
                log_auditoria(f"Erro ao processar período do estudo {estudo_info['nome']}: {e}", 
                             "ERROR", self.log_path)
                print(f"   ❌ Erro ao processar período: {e}")
                print("\nERRO: Período do estudo inválido. Solicite novos arquivos.")
                return False
            
            # Procurar em quais arquivos existem dados neste período
            arquivos_com_dados = []
            for arquivo_idx, df_arquivo in self.dados_por_arquivo.items():
                # Verificar se há dados no período
                df_filtrado = df_arquivo[(df_arquivo['timestamp'] >= data_inicio) & 
                                        (df_arquivo['timestamp'] <= data_fim)]
                if len(df_filtrado) > 0:
                    arquivos_com_dados.append(arquivo_idx)
            
            # Validar resultado
            if len(arquivos_com_dados) == 0:
                log_auditoria(f"Nenhum arquivo contém dados para o estudo {estudo_info['nome']} "
                             f"no período {data_inicio} a {data_fim}", "ERROR", self.log_path)
                print(f"   ❌ Nenhum arquivo contém dados neste período!")
                print("\nERRO: Nenhum arquivo válido encontrado. Solicite novos arquivos.")
                return False
            
            if len(arquivos_com_dados) > 1:
                log_auditoria(f"Múltiplos arquivos contêm dados para o estudo {estudo_info['nome']}: "
                             f"{arquivos_com_dados}", "ERROR", self.log_path)
                print(f"   ❌ Múltiplos arquivos contêm dados neste período: {arquivos_com_dados}")
                print("\nERRO: Ambiguidade detectada. Solicite novos arquivos com períodos distintos.")
                return False
            
            # Atribuir arquivo ao estudo
            arquivo_idx = arquivos_com_dados[0]
            estudo_info['arquivo_idx'] = arquivo_idx
            print(f"   ✅ Arquivo encontrado: {self.arquivos[arquivo_idx - 1]['nome']}")
            log_auditoria(f"Estudo {estudo_info['nome']}: Arquivo {arquivo_idx} detectado automaticamente", 
                         "INFO", self.log_path)
        
        # Criar estudos de temperatura e umidade
        for estudo_info in self.estudos_info:
            arquivo_idx = estudo_info['arquivo_idx']
            
            # Criar estudo de temperatura
            estudo_temp = {
                'nome': estudo_info['nome'],
                'tipo': 'temperatura',
                'arquivo_idx': arquivo_idx,
                'data_inicio': estudo_info['data_inicio'],
                'hora_inicio': estudo_info['hora_inicio'],
                'duracao_horas': estudo_info['duracao_horas'],
                'duracao_minutos': estudo_info['duracao_minutos'],
                'unidade_tempo': estudo_info['unidade_tempo'],
                'teste_informativo': estudo_info['teste_informativo']
            }
            self.estudos.append(estudo_temp)
            
            # Criar estudo de umidade se solicitado E se houver dados
            if gerar_umidade and self.sensores_por_arquivo[arquivo_idx]['umidade']:
                estudo_umidade = {
                    'nome': estudo_info['nome'] + ' (Umidade)',
                    'tipo': 'umidade',
                    'arquivo_idx': arquivo_idx,
                    'data_inicio': estudo_info['data_inicio'],
                    'hora_inicio': estudo_info['hora_inicio'],
                    'duracao_horas': estudo_info['duracao_horas'],
                    'duracao_minutos': estudo_info['duracao_minutos'],
                    'unidade_tempo': estudo_info['unidade_tempo'],
                    'teste_informativo': estudo_info['teste_informativo']
                }
                self.estudos.append(estudo_umidade)
        
        # Perguntar sobre sensores de docas se for galpão
        if self.tipo_equipamento == "galpao":
            print("\n" + "=" * 80)
            print("SENSORES DE DOCAS (GALPÃO)")
            print("=" * 80)
            if len(self.arquivos) == 1:
                # Perguntar apenas uma vez
                print("\nQuais são os sensores das docas para este estudo?")
                print("Sensores disponíveis:")
                
                # Pegar todos os sensores únicos de todos os arquivos
                todos_sensores = set()
                for arquivo_idx in self.sensores_por_arquivo.keys():
                    sensores_temp = self.sensores_por_arquivo[arquivo_idx]['temp']
                    todos_sensores.update(sensores_temp.keys())
                
                sensores_lista = sorted(list(todos_sensores))
                for idx, sensor in enumerate(sensores_lista, 1):
                    print(f"   {idx}. {sensor}")
                print(f"   0. Nenhum sensor de doca")
                
                print("\nVocê pode informar os números da lista (ex: 1, 2, 3) ou os nomes dos sensores (ex: S27, S28, S29)")
                escolha = input("Escolha os sensores de doca (separados por vírgula): ").strip()
                
                docas_lista = []
                if escolha != "0" and escolha:
                    try:
                        # Tentar interpretar como números
                        partes = [x.strip() for x in escolha.split(",") if x.strip()]
                        for parte in partes:
                            if parte.isdigit():
                                idx = int(parte)
                                if 1 <= idx <= len(sensores_lista):
                                    docas_lista.append(sensores_lista[idx - 1])
                            else:
                                # Tentar como nome de sensor (normalizado)
                                parte_norm = normalizar_texto(parte)
                                for sensor in sensores_lista:
                                    if normalizar_texto(sensor) == parte_norm:
                                        if sensor not in docas_lista:
                                            docas_lista.append(sensor)
                                        break
                        if docas_lista:
                            print(f"   ✅ Sensores de doca selecionados: {', '.join(docas_lista)}")
                        else:
                            print(f"   Nenhum sensor de doca válido selecionado")
                    except Exception as e:
                        print(f"   Erro ao processar seleção: {e}")
                        docas_lista = []
                
                # Atribuir para todos os estudos (já que é só 1 arquivo)
                for idx_est in range(1, len(self.estudos) + 1):
                    self.sensores_doca_por_estudo[idx_est] = docas_lista
            else:
                # Perguntar por estudo
                for idx_est, estudo in enumerate(self.estudos, 1):
                    # Perguntar apenas para estudos de temperatura (umidade herda se houver)
                    if estudo['tipo'] == 'temperatura':
                        arquivo_idx = estudo['arquivo_idx']
                        print(f"\nEstudo: {estudo['nome']}")
                        print("Quais são os sensores das docas para este estudo?")
                        print("Sensores disponíveis:")
                        
                        sensores_temp = self.sensores_por_arquivo[arquivo_idx]['temp']
                        sensores_lista = sorted(list(sensores_temp.keys()))
                        for idx, sensor in enumerate(sensores_lista, 1):
                            print(f"   {idx}. {sensor}")
                        print(f"   0. Nenhum sensor de doca")
                        
                        print("\nVocê pode informar os números da lista (ex: 1, 2, 3) ou os nomes dos sensores (ex: S27, S28, S29)")
                        escolha = input("Escolha os sensores de doca (separados por vírgula): ").strip()
                        
                        docas_lista = []
                        if escolha != "0" and escolha:
                            try:
                                # Tentar interpretar como números
                                partes = [x.strip() for x in escolha.split(",") if x.strip()]
                                for parte in partes:
                                    if parte.isdigit():
                                        idx = int(parte)
                                        if 1 <= idx <= len(sensores_lista):
                                            docas_lista.append(sensores_lista[idx - 1])
                                    else:
                                        # Tentar como nome de sensor (normalizado)
                                        parte_norm = normalizar_texto(parte)
                                        for sensor in sensores_lista:
                                            if normalizar_texto(sensor) == parte_norm:
                                                if sensor not in docas_lista:
                                                    docas_lista.append(sensor)
                                                break
                                if docas_lista:
                                    print(f"   ✅ Sensores de doca selecionados: {', '.join(docas_lista)}")
                                else:
                                    print(f"   Nenhum sensor de doca válido selecionado")
                            except Exception as e:
                                print(f"   Erro ao processar seleção: {e}")
                                docas_lista = []
                        
                        self.sensores_doca_por_estudo[idx_est] = docas_lista
                        # Se houver umidade correspondente, herdar
                        # Vamos associar o mesmo docas_lista ao estudo de umidade correspondente se houver
                        
                # Sincronizar docas para estudos de umidade
                for idx_est, estudo in enumerate(self.estudos, 1):
                    if estudo['tipo'] == 'umidade':
                        # Achar o estudo de temperatura correspondente (mesmo nome sem ' (Umidade)')
                        nome_temp = estudo['nome'].replace(' (Umidade)', '')
                        for idx_t, est_t in enumerate(self.estudos, 1):
                            if est_t['tipo'] == 'temperatura' and est_t['nome'] == nome_temp:
                                self.sensores_doca_por_estudo[idx_est] = self.sensores_doca_por_estudo.get(idx_t, [])
                                break

        print(f"\n✅ {len(self.estudos)} estudo(s) será(ão) gerado(s)")
        
        # Log de rastreabilidade
        for idx, estudo in enumerate(self.estudos, 1):
            log_auditoria(f"Estudo {idx}: {estudo['nome']} (arquivo_idx={estudo['arquivo_idx']}, tipo={estudo['tipo']})", 
                         "INFO", self.log_path)
        
        return True
    
    def filtrar_dados_por_periodo(self, df, data_inicio_str, hora_inicio_str, duracao_horas):
        """Filtra dados pelo período específico do estudo"""
        try:
            # Converter strings para datetime
            data_inicio = pd.to_datetime(f"{data_inicio_str} {hora_inicio_str}", 
                                        format='%d/%m/%Y %H:%M', dayfirst=True)
            data_fim = data_inicio + timedelta(hours=duracao_horas)
            
            # Filtrar dados dentro do período
            df_filtrado = df[(df['timestamp'] >= data_inicio) & (df['timestamp'] <= data_fim)].copy()
            
            if len(df_filtrado) == 0:
                print(f"Nenhum dado encontrado no período: {data_inicio} a {data_fim}")
                return None
            
            print(f"   Período: {data_inicio.strftime('%d/%m/%Y %H:%M')} a {data_fim.strftime('%d/%m/%Y %H:%M')}")
            print(f"   Registros no período: {len(df_filtrado)}")
            
            return df_filtrado
            
        except Exception as e:
            print(f"Erro ao filtrar período: {e}")
            return None
    
    def calcular_maturacao_para_estudo(self, idx_estudo, estudo, df_estudo, arquivo_idx):
        """
        Calcula tempo de maturacao e estabilizacao para um estudo de freezer/container.
        Armazena resultado em self.dados_maturacao[idx_estudo]
        """
        if self.tipo_equipamento not in ["freezer", "container"]:
            return
        
        if self.modo_equipamento != "maturacao":
            return
        
        try:
            # Obter sensores internos deste arquivo
            sensores_dict = self.sensores_por_arquivo[arquivo_idx]['temp']
            sensor_externo = self.sensor_externo_por_arquivo.get(arquivo_idx, "")
            
            # Calcular tempo de maturacao
            resultado_maturacao = calcular_tempo_maturacao(
                df_estudo, sensores_dict, sensor_externo,
                self.limite_min_temp, self.limite_max_temp
            )
            
            # Se atingiu maturacao, calcular estabilizacao
            resultado_estabilizacao = None
            if resultado_maturacao['passou']:
                resultado_estabilizacao = calcular_estabilizacao(
                    df_estudo, sensores_dict, sensor_externo,
                    self.limite_min_temp, self.limite_max_temp,
                    resultado_maturacao['data_hora_maturacao'],
                    duracao_estabilizacao_horas=24
                )
            
            # Armazenar resultado
            self.dados_maturacao[idx_estudo] = {
                'maturacao': resultado_maturacao,
                'estabilizacao': resultado_estabilizacao,
                'passou_completo': resultado_maturacao['passou'] and (resultado_estabilizacao['passou'] if resultado_estabilizacao else False)
            }
            
            print(f"   Maturacao calculada: {resultado_maturacao['tempo_maturacao_dias']}")
            if resultado_estabilizacao:
                status_estab = 'MANTIDA' if resultado_estabilizacao['passou'] else 'NAO MANTIDA'
                print(f"   Estabilizacao: {status_estab}")
        
        except Exception as e:
            print(f"Erro ao calcular maturacao: {e}")
            self.dados_maturacao[idx_estudo] = {
                'maturacao': {'passou': False, 'motivo': str(e)},
                'estabilizacao': None,
                'passou_completo': False
            }
    
    def gerar_grafico_estudo(self, df_estudo, nome_estudo, tipo_estudo, arquivo_idx):
        """Gera gráfico para um estudo específico"""
        # Aumentada a altura de 6 para 10 para melhorar a legibilidade
        fig, ax = plt.subplots(figsize=(16, 10))
        
        # Obter sensor externo deste arquivo
        sensor_externo = self.sensor_externo_por_arquivo.get(arquivo_idx, "")
        
        if tipo_estudo == 'temperatura':
            # Obter sensores do arquivo deste estudo
            sensores_dict = self.sensores_por_arquivo[arquivo_idx]['temp']
            
            # Coletar colunas de sensores internos (exceto sensor externo)
            colunas_sensores = [col for sensor, col in sensores_dict.items() 
                              if sensor != sensor_externo]
            
            if colunas_sensores:
                # 1. Plotar TODOS os sensores individuais com cores distintas e LEGENDA VISÍVEL (zorder=2)
                # Usar paleta de cores com mais opções para diferenciar sensores
                cores_sensores = plt.cm.tab20(np.linspace(0, 1, len(colunas_sensores)))
                for idx, col_sensor in enumerate(colunas_sensores):
                    # Obter nome do sensor da chave do dicionário
                    nome_sensor = [k for k, v in sensores_dict.items() if v == col_sensor][0]
                    ax.plot(df_estudo['timestamp'], df_estudo[col_sensor], 
                           linewidth=1.5, alpha=0.75, color=cores_sensores[idx], 
                           label=nome_sensor, zorder=2)
                
                # 2. Calcular e plotar envelope (máximo e mínimo)
                df_temp = df_estudo[colunas_sensores].copy()
                maximo = df_temp.max(axis=1)
                minimo = df_temp.min(axis=1)
                
                # Encontrar qual sensor atingiu a máxima e a mínima
                valor_maximo_global = maximo.max()
                valor_minimo_global = minimo.min()
                
                # Encontrar qual sensor atingiu a máxima
                idx_maximo = maximo.idxmax()
                sensor_maximo = None
                for sensor, col in sensores_dict.items():
                    if col in colunas_sensores and df_estudo.loc[idx_maximo, col] == valor_maximo_global:
                        sensor_maximo = sensor
                        break
                
                # Encontrar qual sensor atingiu a mínima
                idx_minimo = minimo.idxmin()
                sensor_minimo = None
                for sensor, col in sensores_dict.items():
                    if col in colunas_sensores and df_estudo.loc[idx_minimo, col] == valor_minimo_global:
                        sensor_minimo = sensor
                        break
                
                # Criar labels com informações de qual sensor atingiu máxima e mínima
                label_maximo = f'Máximo ({sensor_maximo}: {valor_maximo_global:.1f}°C)' if sensor_maximo else f'Máximo ({valor_maximo_global:.1f}°C)'
                label_minimo = f'Mínimo ({sensor_minimo}: {valor_minimo_global:.1f}°C)' if sensor_minimo else f'Mínimo ({valor_minimo_global:.1f}°C)'
                
                ax.plot(df_estudo['timestamp'], maximo, linewidth=3.2, label=label_maximo, 
                       color='#d62728', linestyle='-', zorder=3, alpha=0.9)
                ax.plot(df_estudo['timestamp'], minimo, linewidth=3.2, label=label_minimo, 
                       color='#1f77b4', linestyle='-', zorder=3, alpha=0.9)
            
            # 3. Plotar limites de aceitação com alto contraste
            ax.axhline(y=self.limite_max_temp, color='#ff7f0e', linestyle='--', linewidth=3.0, 
                      label=f'Limite Máx. ({self.limite_max_temp}°C)', zorder=5, alpha=0.95)
            ax.axhline(y=self.limite_min_temp, color='#2ca02c', linestyle='--', linewidth=3.0, 
                      label=f'Limite Mín. ({self.limite_min_temp}°C)', zorder=5, alpha=0.95)
            
            ax.set_title(f'Qualificação Térmica - {self.tag}', 
                        fontsize=14, fontweight='bold', pad=15, color='#1f1f1f')
            ax.set_ylabel('Temperatura (°C)', fontsize=11, fontweight='bold', color='#1f1f1f')
        
        else:  # umidade
            sensores_dict = self.sensores_por_arquivo[arquivo_idx]['umidade']
            
            # Coletar colunas de sensores internos (exceto sensor externo)
            colunas_sensores = [col for sensor, col in sensores_dict.items() 
                              if sensor != sensor_externo]
            
            if colunas_sensores:
                # 1. Plotar TODOS os sensores individuais com cores distintas e LEGENDA VISÍVEL (zorder=2)
                # Usar paleta de cores com mais opções para diferenciar sensores
                cores_sensores = plt.cm.tab20(np.linspace(0, 1, len(colunas_sensores)))
                for idx, col_sensor in enumerate(colunas_sensores):
                    # Obter nome do sensor da chave do dicionário
                    nome_sensor = [k for k, v in sensores_dict.items() if v == col_sensor][0]
                    ax.plot(df_estudo['timestamp'], df_estudo[col_sensor], 
                           linewidth=1.5, alpha=0.75, color=cores_sensores[idx], 
                           label=nome_sensor, zorder=2)
                
                # 2. Calcular e plotar envelope (máximo e mínimo)
                df_temp = df_estudo[colunas_sensores].copy()
                maximo = df_temp.max(axis=1)
                minimo = df_temp.min(axis=1)
                
                # Encontrar qual sensor atingiu a máxima e a mínima
                valor_maximo_global = maximo.max()
                valor_minimo_global = minimo.min()
                
                # Encontrar qual sensor atingiu a máxima
                idx_maximo = maximo.idxmax()
                sensor_maximo = None
                for sensor, col in sensores_dict.items():
                    if col in colunas_sensores and df_estudo.loc[idx_maximo, col] == valor_maximo_global:
                        sensor_maximo = sensor
                        break
                
                # Encontrar qual sensor atingiu a mínima
                idx_minimo = minimo.idxmin()
                sensor_minimo = None
                for sensor, col in sensores_dict.items():
                    if col in colunas_sensores and df_estudo.loc[idx_minimo, col] == valor_minimo_global:
                        sensor_minimo = sensor
                        break
                
                # Criar labels com informações de qual sensor atingiu máxima e mínima
                label_maximo = f'Máximo ({sensor_maximo}: {valor_maximo_global:.1f}%)' if sensor_maximo else f'Máximo ({valor_maximo_global:.1f}%)'
                label_minimo = f'Mínimo ({sensor_minimo}: {valor_minimo_global:.1f}%)' if sensor_minimo else f'Mínimo ({valor_minimo_global:.1f}%)'
                
                ax.plot(df_estudo['timestamp'], maximo, linewidth=3.2, label=label_maximo, 
                       color='#d62728', linestyle='-', zorder=3, alpha=0.9)
                ax.plot(df_estudo['timestamp'], minimo, linewidth=3.2, label=label_minimo, 
                       color='#1f77b4', linestyle='-', zorder=3, alpha=0.9)
            
            # 3. Plotar limites de aceitação com alto contraste
            ax.axhline(y=self.limite_max_umidade, color='#ff7f0e', linestyle='--', linewidth=3.0, 
                      label=f'Limite Máx. ({self.limite_max_umidade}%)', zorder=5, alpha=0.95)
            ax.axhline(y=self.limite_min_umidade, color='#2ca02c', linestyle='--', linewidth=3.0, 
                      label=f'Limite Mín. ({self.limite_min_umidade}%)', zorder=5, alpha=0.95)
            
            ax.set_title(f'Qualificação de Umidade - {self.tag}', 
                        fontsize=14, fontweight='bold', pad=15, color='#1f1f1f')
            ax.set_ylabel('Umidade (%)', fontsize=11, fontweight='bold', color='#1f1f1f')
        
        # Configuração do eixo X
        ax.set_xlabel('Data/Hora', fontsize=11, fontweight='bold', color='#1f1f1f')
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m %H:%M'))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right', fontsize=9)
        
        # Grid profissional
        ax.grid(True, alpha=0.25, linestyle='-', linewidth=0.5, color='gray')
        ax.set_axisbelow(True)
        
        # Fontes dos eixos
        ax.tick_params(axis='both', which='major', labelsize=9, colors='#1f1f1f')
        
        # Legenda com TODOS os sensores, posicionada fora do plot (lateral direita)
        # Usar bbox_to_anchor para posicionar a legenda fora da area do grafico
        legend = ax.legend(loc='center left', bbox_to_anchor=(1, 0.5), fontsize=8, 
                          framealpha=0.95, edgecolor='#cccccc', fancybox=True, 
                          shadow=True, ncol=1)
        legend.get_frame().set_linewidth(1.0)
        legend.get_frame().set_facecolor('white')
        
        # Melhorar espaçamento
        fig.tight_layout(pad=1.5)
        
        # Salvar com alta qualidade
        buffer = BytesIO()
        plt.savefig(buffer, format='png', dpi=400, bbox_inches='tight', 
                   facecolor='white', edgecolor='none', pad_inches=0.3)
        buffer.seek(0)
        plt.close(fig)
        
        return buffer
    
    def gerar_pdf(self):
        """Gera PDF consolidado"""
        print("\nGerando PDF...")
        
        # NOVO: Calcular maturacao se necessario (por estudo)
        if self.tipo_equipamento in ["freezer", "container"]:
            print("   Calculando tempos de maturacao por estudo...")
            for idx, estudo in enumerate(self.estudos, 1):
                if estudo['tipo'] != 'temperatura':
                    continue
                # Verificar se este estudo especifico eh de maturacao
                if not estudo.get('teste_maturacao', False):
                    continue
                arquivo_idx = estudo['arquivo_idx']
                df_est = self.dados_por_arquivo[arquivo_idx].copy()
                df_est_filtrado = self.filtrar_dados_por_periodo(
                    df_est,
                    estudo['data_inicio'],
                    estudo['hora_inicio'],
                    estudo['duracao_horas']
                )
                if df_est_filtrado is not None and len(df_est_filtrado) > 0:
                    self.calcular_maturacao_para_estudo(idx, estudo, df_est_filtrado, arquivo_idx)
        
        downloads_path = str(Path.home() / "Downloads")
        nome_arquivo = f"relatorio_{self.tag}_{len(self.estudos)}_estudos_gxp.pdf"
        arquivo_completo = os.path.join(downloads_path, nome_arquivo)
        
        doc = SimpleDocTemplate(
            arquivo_completo,
            pagesize=landscape(A4),
            rightMargin=10*mm,
            leftMargin=10*mm,
            topMargin=15*mm,
            bottomMargin=15*mm
        )
        
        elements = []
        styles = getSampleStyleSheet()
        pagina_atual = 1  # Inicializar contador de páginas
        
        # ADICIONAR AS TRÊS NOVAS PÁGINAS NO INÍCIO DO RELATÓRIO (BEM NO INÍCIO, ANTES DE TUDO)
        print("   Gerando Página 1: Resumo dos estudos...")
        elements.extend(self._criar_pagina_resumo_estudos(styles))
        elements.append(PageBreak())
        pagina_atual += 1
        
        print("   Gerando Página 2: Resumo dos resultados e conclusões...")
        elements.extend(self._criar_pagina_resumo_resultados(styles))
        elements.append(PageBreak())
        pagina_atual += 1
        
        print("   Gerando Página 3: Pontos críticos identificados...")
        elements.extend(self._criar_pagina_pontos_criticos(styles))
        elements.append(PageBreak())
        pagina_atual += 1

        # Se múltiplos estudos: adicionar capa e índice
        if len(self.estudos) > 1:
            print("   Gerando capa...")
            elements.extend(self._criar_capa(styles))
            elements.append(PageBreak())
            pagina_atual += 1
            
            print("   Gerando índice...")
            elements.extend(self._criar_indice(styles))
            elements.append(PageBreak())
            pagina_atual += 1
        
        # Loop sobre ESTUDOS, não sobre arquivos
        for idx, estudo in enumerate(self.estudos, 1):
            print(f"   Gerando estudo {idx}: {estudo['nome']}...")
            
            # Usar dados do arquivo específico deste estudo
            arquivo_idx = estudo['arquivo_idx']
            df_estudo = self.dados_por_arquivo[arquivo_idx].copy()
            
            # FILTRAR DADOS PELO PERÍODO DO ESTUDO
            df_estudo = self.filtrar_dados_por_periodo(
                df_estudo,
                estudo['data_inicio'],
                estudo['hora_inicio'],
                estudo['duracao_horas']
            )
            
            if df_estudo is None or len(df_estudo) == 0:
                print(f"   Nenhum dado para o estudo {idx}")
                continue
            
            # Adicionar arquivo_idx ao dataframe para rastreamento
            df_estudo['arquivo_idx'] = arquivo_idx
            
            if estudo['tipo'] == 'temperatura':
                # Calcular temperaturas (excluindo sensor externo)
                sensores_dict = self.sensores_por_arquivo[arquivo_idx]['temp']
                colunas_temp = list(sensores_dict.values())
                
                # Obter sensor externo deste arquivo
                sensor_externo = self.sensor_externo_por_arquivo.get(arquivo_idx, "")
                
                # Se houver sensor externo, removê-lo dos cálculos
                if sensor_externo and sensor_externo in sensores_dict:
                    col_sensor_externo = sensores_dict[sensor_externo]
                    colunas_temp_calc = [col for col in colunas_temp if col != col_sensor_externo]
                else:
                    colunas_temp_calc = colunas_temp
                
                # Calcular apenas com sensores internos
                if colunas_temp_calc:
                    df_temp = df_estudo[colunas_temp_calc]
                    df_estudo['TEMP_MAXIMA'] = df_temp.max(axis=1)
                    df_estudo['TEMP_MINIMA'] = df_temp.min(axis=1)
                    df_estudo['TEMP_MEDIA'] = df_temp.mean(axis=1)
                else:
                    # Se não houver sensores internos, usar todos
                    df_temp = df_estudo[colunas_temp]
                    df_estudo['TEMP_MAXIMA'] = df_temp.max(axis=1)
                    df_estudo['TEMP_MINIMA'] = df_temp.min(axis=1)
                    df_estudo['TEMP_MEDIA'] = df_temp.mean(axis=1)
            
            else:  # umidade
                # Calcular umidades (excluindo sensor externo)
                # Verificar se há dados de umidade
                sensores_dict = self.sensores_por_arquivo[arquivo_idx]['umidade']
                if sensores_dict:
                    colunas_umidade = list(sensores_dict.values())
                    
                    # Obter sensor externo deste arquivo
                    sensor_externo = self.sensor_externo_por_arquivo.get(arquivo_idx, "")
                    
                    # Se houver sensor externo, removê-lo dos cálculos
                    if sensor_externo and sensor_externo in sensores_dict:
                        col_sensor_externo = sensores_dict[sensor_externo]
                        colunas_umid_calc = [col for col in colunas_umidade if col != col_sensor_externo]
                    else:
                        colunas_umid_calc = colunas_umidade
                    
                    # Calcular apenas com sensores internos
                    if colunas_umid_calc:
                        df_umidade = df_estudo[colunas_umid_calc]
                        df_estudo['UMIDADE_MAXIMA'] = df_umidade.max(axis=1)
                        df_estudo['UMIDADE_MINIMA'] = df_umidade.min(axis=1)
                        df_estudo['UMIDADE_MEDIA'] = df_umidade.mean(axis=1)
                    else:
                        # Se não houver sensores internos, usar todos
                        df_umidade = df_estudo[colunas_umidade]
                        df_estudo['UMIDADE_MAXIMA'] = df_umidade.max(axis=1)
                        df_estudo['UMIDADE_MINIMA'] = df_umidade.min(axis=1)
                        df_estudo['UMIDADE_MEDIA'] = df_umidade.mean(axis=1)
                else:
                    print(f"   Nenhum dado de umidade disponível para o estudo {idx}")
                    continue
            # Gráfico e Resumo na mesma página (lado a lado)
            # Registrar página de início deste estudo
            pagina_inicio = pagina_atual
            
            elements.extend(self._criar_pagina_grafico_resumo_estudo(styles, estudo, df_estudo, arquivo_idx))
            elements.append(PageBreak())
            pagina_atual += 1
            
            # Dados brutos
            paginas_dados = self._criar_paginas_dados_estudo(styles, estudo, df_estudo, arquivo_idx)
            elements.extend(paginas_dados)
            # Contar quantas páginas os dados brutos ocupam
            num_registros = len(df_estudo)
            registros_por_pagina = 17
            paginas_dados_count = (num_registros + registros_por_pagina - 1) // registros_por_pagina
            pagina_atual += paginas_dados_count
            
            # Registrar página de fim deste estudo
            pagina_fim = pagina_atual - 1
            self.paginas_estudos[idx] = (pagina_inicio, pagina_fim)
            
            if idx < len(self.estudos):
                elements.append(PageBreak())
                pagina_atual += 1
        
        # Construir PDF com rodapé customizado
        print("   Construindo PDF final...")
        
        # Calcular total de páginas (aproximado)
        self.total_paginas = len(elements)
        
        # Se múltiplos estudos, reconstruir com índice atualizado
        if len(self.estudos) > 1:
            # ── ESTRATÉGIA: renderizar em buffer para capturar páginas reais ──
            # O ReportLab quebra páginas automaticamente quando o conteúdo não cabe.
            # A única forma de saber as páginas reais é renderizar e capturar via callback.
            
            import io as _io
            
            # Marcadores de início de estudo inseridos como Flowables especiais
            class _EstudoMarker(Flowable):
                """Flowable invisível que registra em qual página o estudo começa."""
                def __init__(self, estudo_idx, paginas_dict):
                    Flowable.__init__(self)
                    self.estudo_idx = estudo_idx
                    self.paginas_dict = paginas_dict
                    self.width = 0
                    self.height = 0
                
                def draw(self):
                    page_num = self.canv.getPageNumber()
                    idx = self.estudo_idx
                    if idx not in self.paginas_dict:
                        self.paginas_dict[idx] = [page_num, page_num]
                    else:
                        self.paginas_dict[idx][0] = min(self.paginas_dict[idx][0], page_num)
            
            class _EstudoFimMarker(Flowable):
                """Flowable invisível que registra em qual página o estudo termina."""
                def __init__(self, estudo_idx, paginas_dict):
                    Flowable.__init__(self)
                    self.estudo_idx = estudo_idx
                    self.paginas_dict = paginas_dict
                    self.width = 0
                    self.height = 0
                
                def draw(self):
                    page_num = self.canv.getPageNumber()
                    idx = self.estudo_idx
                    if idx not in self.paginas_dict:
                        self.paginas_dict[idx] = [page_num, page_num]
                    else:
                        self.paginas_dict[idx][1] = page_num
            
            # ── PASSO 1: montar elementos com marcadores (SEM índice correto ainda) ──
            paginas_reais = {}  # {estudo_idx: [inicio, fim]}
            elements_medicao = []
            
            # Capa
            elements_medicao.extend(self._criar_capa(styles))
            elements_medicao.append(PageBreak())
            # Anexo 1
            elements_medicao.extend(self._criar_pagina_resumo_estudos(styles))
            elements_medicao.append(PageBreak())
            # Anexo 2
            elements_medicao.extend(self._criar_pagina_resumo_resultados(styles))
            elements_medicao.append(PageBreak())
            # Anexo 3
            elements_medicao.extend(self._criar_pagina_pontos_criticos(styles))
            elements_medicao.append(PageBreak())
            # Anexo 4 (índice placeholder — 1 página)
            elements_medicao.extend(self._criar_indice(styles))
            elements_medicao.append(PageBreak())
            
            # Estudos com marcadores
            dfs_estudo_cache = {}
            for idx, estudo in enumerate(self.estudos, 1):
                arquivo_idx = estudo['arquivo_idx']
                df_estudo = self.dados_por_arquivo[arquivo_idx].copy()
                df_estudo = self.filtrar_dados_por_periodo(
                    df_estudo, estudo['data_inicio'], estudo['hora_inicio'], estudo['duracao_horas']
                )
                if df_estudo is None or len(df_estudo) == 0:
                    continue
                df_estudo['arquivo_idx'] = arquivo_idx
                
                if estudo['tipo'] == 'temperatura':
                    sensores_dict = self.sensores_por_arquivo[arquivo_idx]['temp']
                    colunas_temp = list(sensores_dict.values())
                    sensor_externo = self.sensor_externo_por_arquivo.get(arquivo_idx, "")
                    if sensor_externo and sensor_externo in sensores_dict:
                        col_sensor_externo = sensores_dict[sensor_externo]
                        colunas_calc = [c for c in colunas_temp if c != col_sensor_externo]
                    else:
                        colunas_calc = colunas_temp
                    df_t = df_estudo[colunas_calc if colunas_calc else colunas_temp]
                    df_estudo['TEMP_MAXIMA'] = df_t.max(axis=1)
                    df_estudo['TEMP_MINIMA'] = df_t.min(axis=1)
                    df_estudo['TEMP_MEDIA'] = df_t.mean(axis=1)
                else:
                    sensores_dict = self.sensores_por_arquivo[arquivo_idx]['umidade']
                    if not sensores_dict:
                        continue
                    colunas_umidade = list(sensores_dict.values())
                    sensor_externo = self.sensor_externo_por_arquivo.get(arquivo_idx, "")
                    if sensor_externo and sensor_externo in sensores_dict:
                        col_sensor_externo = sensores_dict[sensor_externo]
                        colunas_calc = [c for c in colunas_umidade if c != col_sensor_externo]
                    else:
                        colunas_calc = colunas_umidade
                    df_u = df_estudo[colunas_calc if colunas_calc else colunas_umidade]
                    df_estudo['UMIDADE_MAXIMA'] = df_u.max(axis=1)
                    df_estudo['UMIDADE_MINIMA'] = df_u.min(axis=1)
                    df_estudo['UMIDADE_MEDIA'] = df_u.mean(axis=1)
                
                dfs_estudo_cache[idx] = (estudo, df_estudo, arquivo_idx)
                
                # Marcador de início
                elements_medicao.append(_EstudoMarker(idx, paginas_reais))
                elements_medicao.extend(self._criar_pagina_grafico_resumo_estudo(styles, estudo, df_estudo, arquivo_idx))
                elements_medicao.append(PageBreak())
                elements_medicao.extend(self._criar_paginas_dados_estudo(styles, estudo, df_estudo, arquivo_idx))
                # Marcador de fim
                elements_medicao.append(_EstudoFimMarker(idx, paginas_reais))
                
                if idx < len(self.estudos):
                    elements_medicao.append(PageBreak())
            
            # ── PASSO 2: renderizar em buffer para capturar páginas reais ──
            buf = _io.BytesIO()
            doc_medicao = SimpleDocTemplate(
                buf,
                pagesize=landscape(A4),
                rightMargin=10*mm, leftMargin=10*mm,
                topMargin=15*mm, bottomMargin=15*mm
            )
            doc_medicao.build(elements_medicao, onFirstPage=self._rodape, onLaterPages=self._rodape)
            
            # Atualizar paginas_estudos com os valores reais capturados
            self.paginas_estudos = {idx: (v[0], v[1]) for idx, v in paginas_reais.items()}
            
            # ── PASSO 3: reconstruir elementos finais com índice correto ──
            elements_final = []
            elements_final.extend(self._criar_capa(styles))
            elements_final.append(PageBreak())
            elements_final.extend(self._criar_pagina_resumo_estudos(styles))
            elements_final.append(PageBreak())
            elements_final.extend(self._criar_pagina_resumo_resultados(styles))
            elements_final.append(PageBreak())
            elements_final.extend(self._criar_pagina_pontos_criticos(styles))
            elements_final.append(PageBreak())
            # Índice agora com páginas corretas
            elements_final.extend(self._criar_indice(styles))
            elements_final.append(PageBreak())
            
            for idx, (estudo, df_estudo, arquivo_idx) in dfs_estudo_cache.items():
                elements_final.extend(self._criar_pagina_grafico_resumo_estudo(styles, estudo, df_estudo, arquivo_idx))
                elements_final.append(PageBreak())
                elements_final.extend(self._criar_paginas_dados_estudo(styles, estudo, df_estudo, arquivo_idx))
                if idx < len(dfs_estudo_cache):
                    elements_final.append(PageBreak())
            
            elements = elements_final
        
        doc.build(elements, onFirstPage=self._rodape, onLaterPages=self._rodape)      
        log_auditoria(f"PDF gerado: {arquivo_completo}", "INFO", self.log_path)
        
        print(f"\n✅ PDF gerado com sucesso!")
        print(f"{arquivo_completo}")
        
        return arquivo_completo
    
    def _criar_cabecalho_anexo1(self, styles):
        """Cria cabeçalho do Anexo 1 para repetição em cada página"""
        cabecalho_elements = []
        
        titulo_anexo = Paragraph(
            "<b>Anexo 1 - Resumo dos estudos</b>",
            ParagraphStyle('TituloAnexo1', parent=styles['Heading1'],
                          fontSize=11, alignment=TA_LEFT, spaceAfter=2)
        )
        cabecalho_elements.append(titulo_anexo)
        
        info_header = Paragraph(
            f"<b>Área/Equipamento:</b> {self.area} | <b>TAG:</b> {self.tag}<br/>"
            f"<b>Critério de temperatura:</b> {self.limite_min_temp}°C a {self.limite_max_temp}°C",
            ParagraphStyle('InfoHeader1', parent=styles['Normal'],
                          fontSize=8, alignment=TA_LEFT, spaceAfter=8)
        )
        cabecalho_elements.append(info_header)
        
        return cabecalho_elements
    
    def _criar_pagina_resumo_estudos(self, styles):
        """Cria Página 1: Resumo dos estudos (Anexo 1)"""
        elements = []
        
        # ANEXO 1 - CABEçALHO (repetido em cada página)
        elements.extend(self._criar_cabecalho_anexo1(styles))
        
        # Cabeçalho da tabela
        tabela_data = [['Estudo', 'Início', 'Fim', 'Duração']]
        total_horas = 0
        
        # Filtrar apenas estudos de temperatura para não duplicar com umidade (já que umidade é o mesmo estudo)
        estudos_filtrados = [e for e in self.estudos if e['tipo'] == 'temperatura']
        if not estudos_filtrados:
            estudos_filtrados = self.estudos
            
        for estudo in estudos_filtrados:
            # Filtrar dados para pegar as datas/horas reais de início e fim
            arquivo_idx = estudo['arquivo_idx']
            df_est = self.dados_por_arquivo[arquivo_idx].copy()
            df_est_filtrado = self.filtrar_dados_por_periodo(
                df_est,
                estudo['data_inicio'],
                estudo['hora_inicio'],
                estudo['duracao_horas']
            )
            
            if df_est_filtrado is not None and len(df_est_filtrado) > 0:
                inicio_real = df_est_filtrado['timestamp'].min().strftime('%d/%m/%Y %H:%M')
                fim_real = df_est_filtrado['timestamp'].max().strftime('%d/%m/%Y %H:%M')
            else:
                inicio_real = f"{estudo['data_inicio']} {estudo['hora_inicio']}"
                # Calcular fim estimado
                dt_ini = pd.to_datetime(f"{estudo['data_inicio']} {estudo['hora_inicio']}", format='%d/%m/%Y %H:%M', dayfirst=True)
                dt_fim = dt_ini + timedelta(hours=estudo['duracao_horas'])
                fim_real = dt_fim.strftime('%d/%m/%Y %H:%M')
                
            # Formatar duração usando a função formatar_duracao_br
            if estudo['unidade_tempo'] == 'H':
                horas = int(estudo['duracao_horas'])
                minutos = int((estudo['duracao_horas'] - horas) * 60)
                duracao_str = formatar_duracao_br(horas, minutos)
            else:
                minutos = int(estudo['duracao_minutos'])
                duracao_str = formatar_duracao_br(0, minutos)
            
            total_horas += estudo['duracao_horas']
            
            tabela_data.append([estudo['nome'], inicio_real, fim_real, duracao_str])
            
        # Adicionar linha de total
        total_horas_int = int(total_horas)
        total_minutos = int((total_horas - total_horas_int) * 60)
        total_str = formatar_duracao_br(total_horas_int, total_minutos)
        tabela_data.append(['Total de horas de testes', '', '', total_str])
        
        col_widths = [100*mm, 60*mm, 60*mm, 60*mm]
        tabela = Table(tabela_data, colWidths=col_widths)
        tabela.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('SPAN', (0, -1), (2, -1)),  # Mesclar as três primeiras colunas na linha de total
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#F2F2F2')),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        
        elements.append(tabela)
        elements.append(Spacer(1, 10*mm))
        
        # Calcular total de sensores utilizados no início e ativos coletados
        # Pegamos o primeiro arquivo como referência de sensores, ou consolidamos de todos os arquivos
        sensores_ativos_total = 0
        tem_sensor_externo_total = False
        
        vistos_internos = set()
        vistos_externos = set()
        for idx in self.sensores_por_arquivo.keys():
            sensores_temp = self.sensores_por_arquivo[idx]['temp']
            sensor_externo = self.sensor_externo_por_arquivo.get(idx, "")
            
            # Sensores internos ativos
            ativos = [s for s in sensores_temp.keys() if s != sensor_externo]
            for s in ativos:
                vistos_internos.add(s)
            
            if sensor_externo and sensor_externo in sensores_temp:
                vistos_externos.add(sensor_externo)
                tem_sensor_externo_total = True
                
        sensores_ativos_total = len(vistos_internos)
        total_sensores_estudo = sensores_ativos_total + len(vistos_externos)
        
        # Texto explicativo detalhando sensores internos e externos
        externo_str = f" e {len(vistos_externos)} sensor externo" if tem_sensor_externo_total else ""
        texto_sensores = Paragraph(
            f"Total de sensores utilizados no início do estudo: <b>{total_sensores_estudo}</b> sensores (<b>{sensores_ativos_total}</b> sensores internos{externo_str}).<br/>"
            f"Total de sensores ativos que conseguimos coletar os dados: <b>{total_sensores_estudo}</b> sensores (<b>{sensores_ativos_total}</b> sensores internos{externo_str}).",
            ParagraphStyle('TextoSensores', parent=styles['Normal'], fontSize=10, leading=14)
        )
        elements.append(texto_sensores)
        
        return elements

    def _criar_cabecalho_anexo2(self, styles):
        """Cria cabeçalho do Anexo 2 para repetição em cada página"""
        cabecalho_elements = []
        
        titulo_anexo = Paragraph(
            "<b>Anexo 2 - Resumo dos resultados e conclusões</b>",
            ParagraphStyle('TituloAnexo2', parent=styles['Heading1'],
                          fontSize=11, alignment=TA_LEFT, spaceAfter=2)
        )
        cabecalho_elements.append(titulo_anexo)
        
        info_header = Paragraph(
            f"<b>Área/Equipamento:</b> {self.area} | <b>TAG:</b> {self.tag}<br/>"
            f"<b>Critério de temperatura:</b> {self.limite_min_temp}°C a {self.limite_max_temp}°C",
            ParagraphStyle('InfoHeader2', parent=styles['Normal'],
                          fontSize=8, alignment=TA_LEFT, spaceAfter=8)
        )
        cabecalho_elements.append(info_header)
        
        return cabecalho_elements
    
    def _criar_pagina_resumo_resultados(self, styles):
        """Cria Página 2: Resumo dos resultados e conclusões (Anexo 2)"""
        elements = []
        
        # ANEXO 2 - CABEÇALHO (repetido em cada página)
        elements.extend(self._criar_cabecalho_anexo2(styles))
        
        # Filtrar apenas estudos de temperatura para obter a numeração correta (Teste 1, Teste 2, etc.)
        estudos_temp = [e for e in self.estudos if e['tipo'] == 'temperatura']
        if not estudos_temp:
            estudos_temp = self.estudos
        
        # Adicionar quebra de página e repetir cabeçalho se houver múltiplos testes
        primeira_iteracao = True
            
        for idx_teste, estudo in enumerate(estudos_temp, 1):
            arquivo_idx = estudo['arquivo_idx']
            df_est = self.dados_por_arquivo[arquivo_idx].copy()
            df_est_filtrado = self.filtrar_dados_por_periodo(
                df_est,
                estudo['data_inicio'],
                estudo['hora_inicio'],
                estudo['duracao_horas']
            )
            
            if df_est_filtrado is None or len(df_est_filtrado) == 0:
                continue
                
            # Obter sensor externo
            sensor_externo = self.sensor_externo_por_arquivo.get(arquivo_idx, "")
            
            # 1. PROCESSAR DADOS DE TEMPERATURA PARA ESTE TESTE
            sensores_dict_temp = self.sensores_por_arquivo[arquivo_idx]['temp']
            colunas_calc_temp = [col for s, col in sensores_dict_temp.items() if s != sensor_externo]
            
            if colunas_calc_temp:
                maximas_temp = [df_est_filtrado[col].dropna().max() if len(df_est_filtrado[col].dropna()) > 0 else np.nan for col in colunas_calc_temp]
                minimas_temp = [df_est_filtrado[col].dropna().min() if len(df_est_filtrado[col].dropna()) > 0 else np.nan for col in colunas_calc_temp]
                medias_temp = [df_est_filtrado[col].dropna().mean() if len(df_est_filtrado[col].dropna()) > 0 else np.nan for col in colunas_calc_temp]
                
                # Remover NaN dos cálculos globais
                maximas_temp_validas = [x for x in maximas_temp if not np.isnan(x)]
                minimas_temp_validas = [x for x in minimas_temp if not np.isnan(x)]
                medias_temp_validas = [x for x in medias_temp if not np.isnan(x)]
                
                max_global_temp = max(maximas_temp_validas) if maximas_temp_validas else 0
                min_global_temp = min(minimas_temp_validas) if minimas_temp_validas else 0
                media_global_temp = np.mean(medias_temp_validas) if medias_temp_validas else 0
                
                # Texto descritivo de temperatura
                if estudo.get('teste_informativo', False):
                    conclusao_temp = "Este teste é de caráter informativo e não possui critério de aceitação."
                else:
                    atende_temp = (min_global_temp >= self.limite_min_temp) and (max_global_temp <= self.limite_max_temp)
                    if atende_temp:
                        conclusao_temp = ""
                    else:
                        conclusao_temp = f"Verificou-se que os valores de temperatura saíram da faixa de trabalho estabelecida de {self.limite_min_temp:.1f}°C a {self.limite_max_temp:.1f}°C."
                
                texto_estudo_temp = Paragraph(
                    f"No estudo da <b>{estudo['nome']}</b> verificou-se uma temperatura "
                    f"máxima de <b>{max_global_temp:.1f}°C</b> e uma temperatura mínima de <b>{min_global_temp:.1f}°C</b>. "
                    f"A temperatura média registrada nos dataloggers foi de <b>{media_global_temp:.1f}°C</b>.",
                    ParagraphStyle('TextoEstudoP2_T', parent=styles['Normal'], fontSize=9, leading=13, spaceAfter=5)
                )
                elements.append(texto_estudo_temp)
                
                # Tabela de resumo de temperatura
                header_t = ['Temperatura', 'Temp. Máxima', 'Temp. Mínima', 'Temp. Média']
                
                # Garantir que as listas não estão vazias antes de calcular
                if maximas_temp_validas:
                    linha_max_t = ['Máxima', f"{max(maximas_temp_validas):.1f}", f"{min(maximas_temp_validas):.1f}", f"{np.mean(maximas_temp_validas):.1f}"]
                else:
                    linha_max_t = ['Máxima', 'ND', 'ND', 'ND']
                
                if minimas_temp_validas:
                    linha_min_t = ['Mínima', f"{max(minimas_temp_validas):.1f}", f"{min(minimas_temp_validas):.1f}", f"{np.mean(minimas_temp_validas):.1f}"]
                else:
                    linha_min_t = ['Mínima', 'ND', 'ND', 'ND']
                
                if medias_temp_validas:
                    linha_med_t = ['Média', f"{max(medias_temp_validas):.1f}", f"{min(medias_temp_validas):.1f}", f"{np.mean(medias_temp_validas):.1f}"]
                else:
                    linha_med_t = ['Média', 'ND', 'ND', 'ND']
                
                tabela_res_data_t = [header_t, linha_max_t, linha_min_t, linha_med_t]
                tabela_res_t = Table(tabela_res_data_t, colWidths=[40*mm, 40*mm, 40*mm, 40*mm])
                tabela_res_t.setStyle(TableStyle([
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#D3D3D3')),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, -1), 8),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('TEXTCOLOR', (1, 1), (1, 1), colors.red),
                    ('TEXTCOLOR', (2, 2), (2, 2), colors.blue),
                    ('TEXTCOLOR', (3, 3), (3, 3), colors.orange),
                    ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
                    ('TOPPADDING', (0, 0), (-1, -1), 3),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ]))
                elements.append(tabela_res_t)
                elements.append(Spacer(1, 4*mm))
                
            # 2. PROCESSAR DADOS DE UMIDADE PARA ESTE TESTE (SE HOUVER ESTUDO DE UMIDADE ASSOCIADO)
            # Verificar se o estudo de umidade existe para este mesmo teste
            estudo_umid_associado = None
            nome_umid_busca = estudo['nome'] + ' (Umidade)'
            for e_u in self.estudos:
                if e_u['tipo'] == 'umidade' and e_u['nome'] == nome_umid_busca:
                    estudo_umid_associado = e_u
                    break
                    
            if estudo_umid_associado:
                sensores_dict_umid = self.sensores_por_arquivo[arquivo_idx]['umidade']
                colunas_calc_umid = [col for s, col in sensores_dict_umid.items() if s != sensor_externo]
                
                if colunas_calc_umid:
                    # Aplicar a mesma regra de dados faltantes: ignorar NaN
                    maximas_umid = [df_est_filtrado[col].dropna().max() if len(df_est_filtrado[col].dropna()) > 0 else np.nan for col in colunas_calc_umid]
                    minimas_umid = [df_est_filtrado[col].dropna().min() if len(df_est_filtrado[col].dropna()) > 0 else np.nan for col in colunas_calc_umid]
                    medias_umid = [df_est_filtrado[col].dropna().mean() if len(df_est_filtrado[col].dropna()) > 0 else np.nan for col in colunas_calc_umid]
                    
                    # Remover NaN dos cálculos globais
                    maximas_umid_validas = [x for x in maximas_umid if not np.isnan(x)]
                    minimas_umid_validas = [x for x in minimas_umid if not np.isnan(x)]
                    medias_umid_validas = [x for x in medias_umid if not np.isnan(x)]
                    
                    max_global_umid = max(maximas_umid_validas) if maximas_umid_validas else 0
                    min_global_umid = min(minimas_umid_validas) if minimas_umid_validas else 0
                    media_global_umid = np.mean(medias_umid_validas) if medias_umid_validas else 0
                    
                    texto_estudo_umid = Paragraph(
                        f"<b>Teste {idx_teste} - {estudo['nome']} (Umidade)</b><br/>"
                        f"No estudo da <b>{estudo['nome']}</b> verificou-se uma umidade "
                        f"máxima de <font color='red'><b>{max_global_umid:.1f}%</b></font> e uma umidade mínima de <font color='blue'><b>{min_global_umid:.1f}%</b></font>. "
                        f"A umidade média registrada nos dataloggers foi de <font color='orange'><b>{media_global_umid:.1f}%</b></font>. "
                        f"Este teste de umidade é de caráter informativo e não possui critério de aceitação.",
                        ParagraphStyle('TextoEstudoP2_U', parent=styles['Normal'], fontSize=9, leading=13, spaceAfter=5)
                    )
                    elements.append(texto_estudo_umid)
                    
                    # Tabela de resumo de umidade
                    header_u = ['Umidade', 'Umid. Máxima', 'Umid. Mínima', 'Umid. Média']
                    
                    # Garantir que as listas não estão vazias antes de calcular
                    if maximas_umid_validas:
                        linha_max_u = ['Máxima', f"{max(maximas_umid_validas):.1f}", f"{min(maximas_umid_validas):.1f}", f"{np.mean(maximas_umid_validas):.1f}"]
                    else:
                        linha_max_u = ['Máxima', 'ND', 'ND', 'ND']
                    
                    if minimas_umid_validas:
                        linha_min_u = ['Mínima', f"{max(minimas_umid_validas):.1f}", f"{min(minimas_umid_validas):.1f}", f"{np.mean(minimas_umid_validas):.1f}"]
                    else:
                        linha_min_u = ['Mínima', 'ND', 'ND', 'ND']
                    
                    if medias_umid_validas:
                        linha_med_u = ['Média', f"{max(medias_umid_validas):.1f}", f"{min(medias_umid_validas):.1f}", f"{np.mean(medias_umid_validas):.1f}"]
                    else:
                        linha_med_u = ['Média', 'ND', 'ND', 'ND']
                    
                    tabela_res_data_u = [header_u, linha_max_u, linha_min_u, linha_med_u]
                    tabela_res_u = Table(tabela_res_data_u, colWidths=[40*mm, 40*mm, 40*mm, 40*mm])
                    tabela_res_u.setStyle(TableStyle([
                        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#D3D3D3')),
                        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                        ('FONTSIZE', (0, 0), (-1, -1), 8),
                        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                        ('TEXTCOLOR', (1, 1), (1, 1), colors.red),
                        ('TEXTCOLOR', (2, 2), (2, 2), colors.blue),
                        ('TEXTCOLOR', (3, 3), (3, 3), colors.orange),
                        ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
                        ('TOPPADDING', (0, 0), (-1, -1), 3),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                    ]))
                    elements.append(tabela_res_u)
                    elements.append(Spacer(1, 5*mm))
            
        return elements

    def _aplicar_sombreamento_pontos_criticos(self, estilo, sensores_ordenados, sensores_consolidados, 
                                               ponto_quente, pontos_quentes_gerais, ponto_frio, ponto_umidade, tipo_equipamento):
        """Aplica sombreamento na tabela consolidada baseado nos pontos críticos identificados"""
        if tipo_equipamento == "refrigerador":
            # Refrigerador: 1 ponto quente e 1 ponto frio
            for idx_s, s in enumerate(sensores_ordenados, 1):
                if s == ponto_quente:
                    estilo.append(('BACKGROUND', (idx_s, 1), (idx_s, 1), colors.HexColor('#FF6B6B')))  # Vermelho para máxima
                if s == ponto_frio:
                    estilo.append(('BACKGROUND', (idx_s, 2), (idx_s, 2), colors.HexColor('#ADD8E6')))  # Azul para mínima
        else:
            # Galpão: 1 ponto quente de doca, 2 pontos quentes gerais, 1 ponto frio
            for idx_s, s in enumerate(sensores_ordenados, 1):
                if s == ponto_quente:
                    estilo.append(('BACKGROUND', (idx_s, 1), (idx_s, 1), colors.HexColor('#FF6B6B')))  # Vermelho para máxima
                if pontos_quentes_gerais and s in pontos_quentes_gerais:
                    estilo.append(('BACKGROUND', (idx_s, 1), (idx_s, 1), colors.HexColor('#FF6B6B')))  # Vermelho para máxima
                if s == ponto_frio:
                    estilo.append(('BACKGROUND', (idx_s, 2), (idx_s, 2), colors.HexColor('#ADD8E6')))  # Azul para mínima

    def _criar_cabecalho_anexo3(self, styles):
        """Cria cabeçalho do Anexo 3 para repetição em cada página"""
        cabecalho_elements = []
        
        titulo_empresa = Paragraph(
            f"<b>{self.empresa}</b>",
            ParagraphStyle('TituloEmpresaP3', parent=styles['Heading1'],
                          fontSize=12, alignment=TA_CENTER, spaceAfter=5)
        )
        cabecalho_elements.append(titulo_empresa)
        
        # Adaptar titulo do Anexo 3 conforme tipo de equipamento
        if self.tipo_equipamento in ["freezer", "container"]:
            if self.modo_equipamento == "maturacao":
                titulo_text = "<b>Anexo 3 - Resultados de Maturacao</b>"
            else:
                titulo_text = "<b>Anexo 3 - Resultados de Conservacao</b>"
        else:
            titulo_text = "<b>Anexo 3 - Pontos Criticos identificados</b>"
        
        titulo_anexo = Paragraph(
            titulo_text,
            ParagraphStyle('TituloAnexo3', parent=styles['Heading1'],
                          fontSize=11, alignment=TA_LEFT, spaceAfter=2)
        )
        cabecalho_elements.append(titulo_anexo)
        
        info_header = Paragraph(
            f"<b>Área/Equipamento:</b> {self.area} | <b>TAG:</b> {self.tag}<br/>"
            f"<b>Critério de temperatura:</b> {self.limite_min_temp}°C a {self.limite_max_temp}°C",
            ParagraphStyle('InfoHeader3', parent=styles['Normal'],
                          fontSize=8, alignment=TA_LEFT, spaceAfter=8)
        )
        cabecalho_elements.append(info_header)
        
        return cabecalho_elements
    
    def _criar_pagina_pontos_criticos(self, styles):
        """Cria Página 3: Pontos Críticos Identificados (ou Resultados para Freezer/Container)"""
        elements = []
        
        # ANEXO 3 - CABEçALHO (repetido em cada página)
        elements.extend(self._criar_cabecalho_anexo3(styles))
        
        # NOVO: Se for freezer/container, usar lógica diferente
        if self.tipo_equipamento in ["freezer", "container"]:
            return self._criar_pagina_resultados_freezer_container(styles, elements)
        
    
    def _criar_pagina_resultados_freezer_container(self, styles, elements):
        """Cria página de resultados para freezer/container (sem pontos críticos)"""
        
        if self.modo_equipamento == "maturacao":
            # ===== MODO MATURAÇÃO =====
            elementos_maturacao = []
            
            # Título
            titulo = Paragraph(
                "<b>Resultados de Maturação</b>",
                ParagraphStyle('TituloMaturacao', parent=styles['Heading2'],
                              fontSize=12, spaceAfter=10)
            )
            elementos_maturacao.append(titulo)
            
            # Tabela de resultados
            tabela_data = [['Estudo', 'Tempo de Maturação', 'Status Estabilização', 'Resultado']]
            
            for idx_estudo, estudo in enumerate(self.estudos, 1):
                if estudo['tipo'] != 'temperatura':
                    continue
                    
                dados = self.dados_maturacao.get(idx_estudo, {})
                resultado_mat = dados.get('maturacao', {})
                resultado_estab = dados.get('estabilizacao', {})
                passou_completo = dados.get('passou_completo', False)
                
                tempo_mat = resultado_mat.get('tempo_maturacao_dias', 'N/A')
                status_estab = 'MANTIDA' if resultado_estab and resultado_estab.get('passou') else 'NÃO MANTIDA'
                resultado_final = 'APROVADO' if passou_completo else 'REPROVADO'
                
                tabela_data.append([
                    estudo['nome'],
                    tempo_mat,
                    status_estab,
                    resultado_final
                ])
            
            # Criar tabela
            col_widths = [80*mm, 70*mm, 70*mm, 60*mm]
            tabela = Table(tabela_data, colWidths=col_widths)
            tabela.setStyle(TableStyle([
                ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ]))
            
            elementos_maturacao.append(tabela)
            elementos_maturacao.append(Spacer(1, 10*mm))
            
            # Detalhes de cada estudo
            for idx_estudo, estudo in enumerate(self.estudos, 1):
                if estudo['tipo'] != 'temperatura':
                    continue
                    
                dados = self.dados_maturacao.get(idx_estudo, {})
                resultado_mat = dados.get('maturacao', {})
                resultado_estab = dados.get('estabilizacao', {})
                
                # Título do estudo
                titulo_estudo = Paragraph(
                    f"<b>{estudo['nome']}</b>",
                    ParagraphStyle('TituloEstudo', parent=styles['Heading3'],
                                  fontSize=10, spaceAfter=5)
                )
                elementos_maturacao.append(titulo_estudo)
                
                # Detalhes
                if resultado_mat.get('passou'):
                    texto_detalhe = f"""
<b>Tempo de Maturação:</b> {resultado_mat.get('tempo_maturacao_dias', 'N/A')}<br/>
<b>Atingido em:</b> {resultado_mat.get('data_hora_maturacao', 'N/A')}<br/>
"""
                    if resultado_estab:
                        if resultado_estab.get('passou'):
                            texto_detalhe += "<b>Estabilização (24h):</b> MANTIDA<br/>"
                        else:
                            sensores_desvio = ', '.join(resultado_estab.get('sensores_com_desvio', []))
                            texto_detalhe += f"<b>Estabilização (24h):</b> NÃO MANTIDA<br/><b>Sensores com desvio:</b> {sensores_desvio}<br/>"
                else:
                    texto_detalhe = f"<b>Status:</b> Maturação não atingida<br/><b>Motivo:</b> {resultado_mat.get('motivo', 'Desconhecido')}<br/>"
                
                paragrafo = Paragraph(texto_detalhe, styles['Normal'])
                elementos_maturacao.append(paragrafo)
                elementos_maturacao.append(Spacer(1, 5*mm))
            
            elements.extend(elementos_maturacao)
            
        else:
            # ===== MODO CONSERVAÇÃO =====
            elementos_conservacao = []
            
            # Título
            titulo = Paragraph(
                "<b>Resultados de Conservação</b>",
                ParagraphStyle('TituloConservacao', parent=styles['Heading2'],
                              fontSize=12, spaceAfter=10)
            )
            elementos_conservacao.append(titulo)
            
            # Tabela de resultados
            tabela_data = [['Estudo', 'Temperatura Máxima', 'Temperatura Mínima', 'Resultado']]
            
            for idx_estudo, estudo in enumerate(self.estudos, 1):
                if estudo['tipo'] != 'temperatura':
                    continue
                
                arquivo_idx = estudo['arquivo_idx']
                df_est = self.dados_por_arquivo[arquivo_idx].copy()
                df_est_filtrado = self.filtrar_dados_por_periodo(
                    df_est,
                    estudo['data_inicio'],
                    estudo['hora_inicio'],
                    estudo['duracao_horas']
                )
                
                if df_est_filtrado is None or len(df_est_filtrado) == 0:
                    tabela_data.append([estudo['nome'], 'N/A', 'N/A', 'SEM DADOS'])
                    continue
                
                # Calcular máximo e mínimo (excluindo sensor externo)
                sensores_dict = self.sensores_por_arquivo[arquivo_idx]['temp']
                sensor_externo = self.sensor_externo_por_arquivo.get(arquivo_idx, "")
                colunas_calc = [col for s, col in sensores_dict.items() if s != sensor_externo]
                
                if colunas_calc:
                    temp_max = df_est_filtrado[colunas_calc].max().max()
                    temp_min = df_est_filtrado[colunas_calc].min().min()
                else:
                    temp_max = 0
                    temp_min = 0
                
                # Verificar se passou no critério
                passou = (temp_max <= self.limite_max_temp) and (temp_min >= self.limite_min_temp)
                resultado = 'APROVADO' if passou else 'REPROVADO'
                
                tabela_data.append([
                    estudo['nome'],
                    f"{temp_max:.1f}°C",
                    f"{temp_min:.1f}°C",
                    resultado
                ])
            
            # Criar tabela
            col_widths = [80*mm, 70*mm, 70*mm, 60*mm]
            tabela = Table(tabela_data, colWidths=col_widths)
            tabela.setStyle(TableStyle([
                ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ]))
            
            elementos_conservacao.append(tabela)
            elementos_conservacao.append(Spacer(1, 10*mm))
            
            # Texto informativo
            texto_info = Paragraph(
                f"<b>Critério de Temperatura:</b> {self.limite_min_temp}°C a {self.limite_max_temp}°C<br/>"
                f"Os equipamentos de conservação foram avaliados quanto ao atendimento ao critério de temperatura estabelecido.",
                styles['Normal']
            )
            elementos_conservacao.append(texto_info)
            
            elements.extend(elementos_conservacao)
        
        return elements
        
        # Juntar todos os testes normais (ignorar informativos)
        dfs_normais = []
        sensores_comuns = None
        
        # Identificar quais estudos são normais
        estudos_normais = [e for e in self.estudos if not e.get('teste_informativo', False) and e['tipo'] == 'temperatura']
        
        if not estudos_normais:
            # Se não houver nenhum estudo normal de temperatura, tentar qualquer um de temperatura
            estudos_normais = [e for e in self.estudos if e['tipo'] == 'temperatura']
            
        for estudo in estudos_normais:
            arquivo_idx = estudo['arquivo_idx']
            df_est = self.dados_por_arquivo[arquivo_idx].copy()
            df_est_filtrado = self.filtrar_dados_por_periodo(
                df_est,
                estudo['data_inicio'],
                estudo['hora_inicio'],
                estudo['duracao_horas']
            )
            if df_est_filtrado is not None and len(df_est_filtrado) > 0:
                dfs_normais.append((df_est_filtrado, arquivo_idx))
                
        if not dfs_normais:
            elements.append(Paragraph("Nenhum dado de teste normal disponível para análise de pontos críticos.", styles['Normal']))
            return elements
            
        # Vamos consolidar as estatísticas de todos os testes normais por sensor
        # Para cada sensor interno, vamos calcular a Máxima Absoluta, Mínima Absoluta e Média Geral considerando todos os testes normais
        estatisticas_sensores = {}
        
        # Pegar todos os sensores internos dos arquivos processados
        for df_filtrado, arquivo_idx in dfs_normais:
            sensores_dict = self.sensores_por_arquivo[arquivo_idx]['temp']
            sensor_externo = self.sensor_externo_por_arquivo.get(arquivo_idx, "")
            sensores_internos = {s: col for s, col in sensores_dict.items() if s != sensor_externo}
            
            for s, col in sensores_internos.items():
                if s not in estatisticas_sensores:
                    estatisticas_sensores[s] = {'maxs': [], 'mins': [], 'sums': [], 'counts': []}
                
                # Coletar valores deste período
                valores = df_filtrado[col].dropna()
                if len(valores) > 0:
                    estatisticas_sensores[s]['maxs'].append(valores.max())
                    estatisticas_sensores[s]['mins'].append(valores.min())
                    estatisticas_sensores[s]['sums'].append(valores.sum())
                    estatisticas_sensores[s]['counts'].append(len(valores))
                    
        # Calcular consolidados finais por sensor
        sensores_consolidados = {}
        for s, dados in estatisticas_sensores.items():
            if dados['maxs']:
                max_abs = max(dados['maxs'])
                min_abs = min(dados['mins'])
                media_geral = sum(dados['sums']) / sum(dados['counts']) if sum(dados['counts']) > 0 else 0
                sensores_consolidados[s] = {
                    'max': max_abs,
                    'min': min_abs,
                    'media': media_geral
                }
                
        if not sensores_consolidados:
            elements.append(Paragraph("Não foi possível consolidar dados dos sensores.", styles['Normal']))
            return elements
            
        # Criar tabelinha consolidada igual à análise de resultados
        # Ordenar sensores numericamente
        import re
        def extrair_numero(nome_sensor):
            numeros = re.findall(r'\d+', nome_sensor)
            if numeros:
                return int(numeros[-1])
            return float('inf')
            
        sensores_ordenados = sorted(sensores_consolidados.keys(), key=extrair_numero)
        
        # Preparar dados para a tabela consolidada
        header = [RotatedText('ESTATÍSTICA', font_size=5)]
        for s in sensores_ordenados:
            header.append(RotatedText(s, font_size=5))
        header.extend([RotatedText('MÁXIMO', font_size=5), RotatedText('MÍNIMO', font_size=5), RotatedText('MÉDIA', font_size=5)])
        
        maximas = ['MÁXIMA']
        minimas = ['MÍNIMA']
        medias = ['MÉDIA']
        
        valores_max = []
        valores_min = []
        valores_med = []
        
        for s in sensores_ordenados:
            maximas.append(f"{sensores_consolidados[s]['max']:.1f}")
            minimas.append(f"{sensores_consolidados[s]['min']:.1f}")
            medias.append(f"{sensores_consolidados[s]['media']:.1f}")
            
            valores_max.append(sensores_consolidados[s]['max'])
            valores_min.append(sensores_consolidados[s]['min'])
            valores_med.append(sensores_consolidados[s]['media'])
            
        # Adicionar as colunas de resumo calculadas no final
        maximas.extend([f"{max(valores_max):.1f}", f"{min(valores_max):.1f}", f"{np.mean(valores_max):.1f}"])
        minimas.extend([f"{max(valores_min):.1f}", f"{min(valores_min):.1f}", f"{np.mean(valores_min):.1f}"])
        medias.extend([f"{max(valores_med):.1f}", f"{min(valores_med):.1f}", f"{np.mean(valores_med):.1f}"])
        
        # Destacar máximas e mínimas globais
        max_global_val = max(valores_max)
        min_global_val = min(valores_min)
        
        tabela_dados = [header, maximas, minimas, medias]
        
        # Calcular larguras responsivas
        col_widths = calcular_larguras_resumo(len(sensores_ordenados), tem_sensor_externo=False)
        tabela_cons = Table(tabela_dados, colWidths=col_widths)
        
        # Estilo da tabela
        estilo = [
            ('GRID', (0, 0), (-1, -1), 0.3, colors.black),
            ('FONTSIZE', (0, 0), (-1, 0), 5),
            ('FONTSIZE', (0, 1), (-1, -1), 4),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('BACKGROUND', (-3, 0), (-1, -1), colors.HexColor('#ADD8E6')),
            ('LEFTPADDING', (0, 0), (-1, -1), 2),
            ('RIGHTPADDING', (0, 0), (-1, -1), 2),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]
        
        # Estilo será aplicado APÓS identificar os pontos críticos
        # Não aplicar ainda - será feito depois do cálculo dos pontos críticos
        
        elements.append(Paragraph("<b>Tabela Consolidada de Testes Mandatórios:</b>", ParagraphStyle('TabConsT', parent=styles['Normal'], fontSize=9, spaceAfter=3)))
        elements.append(tabela_cons)
        elements.append(Spacer(1, 5*mm))
        
        # Armazenar dados de umidade para usar depois
        umidade_consolidada = {}
        if self.tipo_equipamento == "galpao":
            estatisticas_umidade_temp = {}
            for df_filtrado, arquivo_idx in dfs_normais:
                sensores_dict_u = self.sensores_por_arquivo[arquivo_idx]['umidade']
                if sensores_dict_u:
                    sensor_externo = self.sensor_externo_por_arquivo.get(arquivo_idx, "")
                    sensores_internos_u = {s: col for s, col in sensores_dict_u.items() if s != sensor_externo}
                    for s, col in sensores_internos_u.items():
                        if s not in estatisticas_umidade_temp:
                            estatisticas_umidade_temp[s] = []
                        valores = df_filtrado[col].dropna()
                        if len(valores) > 0:
                            estatisticas_umidade_temp[s].extend(valores.tolist())
            
            if estatisticas_umidade_temp:
                for s, vals in estatisticas_umidade_temp.items():
                    if len(vals) > 0:
                        umidade_consolidada[s] = {
                            'max': max(vals),
                            'min': min(vals),
                            'media': np.mean(vals)
                        }
        
        # LÓGICA DE PONTOS CRÍTICOS
        ponto_quente_str = ""
        ponto_frio_str = ""
        ponto_umidade_str = ""
        
        if self.tipo_equipamento == "refrigerador":
            # 1 PONTO QUENTE (Temperatura máxima absoluta, desempate pela média)
            candidatos_quentes = []
            for s, dados in sensores_consolidados.items():
                if dados['max'] == max_global_val:
                    candidatos_quentes.append((s, dados['media']))
            # Desempate pela média (maior média primeiro)
            candidatos_quentes.sort(key=lambda x: x[1], reverse=True)
            ponto_quente = candidatos_quentes[0][0]
            
            # 1 PONTO FRIO (Temperatura mínima absoluta, desempate pela média)
            candidatos_frios = []
            for s, dados in sensores_consolidados.items():
                if dados['min'] == min_global_val:
                    candidatos_frios.append((s, dados['media']))
            # Desempate pela média (menor média primeiro)
            candidatos_frios.sort(key=lambda x: x[1], reverse=False)
            ponto_frio = candidatos_frios[0][0]
            
            ponto_quente_str = f"Ponto Quente: <b>{ponto_quente}</b> com máxima de {max_global_val:.1f}°C (Média: {sensores_consolidados[ponto_quente]['media']:.1f}°C)"
            ponto_frio_str = f"Ponto Frio: <b>{ponto_frio}</b> com mínima de {min_global_val:.1f}°C (Média: {sensores_consolidados[ponto_frio]['media']:.1f}°C)"
            
            # Aplicar sombreamento na tabela consolidada para pontos críticos do refrigerador
            self._aplicar_sombreamento_pontos_criticos(estilo, sensores_ordenados, sensores_consolidados, 
                                                       ponto_quente, None, ponto_frio, None, self.tipo_equipamento)
            
            # Aplicar estilo à tabela agora que temos os pontos críticos
            tabela_cons.setStyle(TableStyle(estilo))
            
            texto_temp_analise = Paragraph(
                f"Os pontos críticos foram identificados através da análise consolidada de todos os testes mandatórios. O ponto quente foi definido como o sensor com a maior temperatura máxima, com desempate pela maior temperatura média. O ponto frio foi definido como o sensor com a menor temperatura mínima, com desempate pela menor temperatura média.<br/><br/>"
                f"• {ponto_quente_str}<br/>"
                f"• {ponto_frio_str}",
                ParagraphStyle('AnaliseP3', parent=styles['Normal'], fontSize=10, leading=15)
            )
            elements.append(texto_temp_analise)
            
        else:  # galpao
            # 3 PONTOS QUENTES:
            # - 1 de Doca (será necessário filtrar pelos sensores das docas informados)
            # - 2 outros pontos quentes gerais (excluindo os já selecionados ou pegando as maiores máximas gerais)
            # Sempre desempata usando a média.
            
            # Pegar sensores de docas informados
            # Como podemos ter docas por estudo, vamos consolidar todos os sensores de docas informados
            # Vamos usar normalização para garantir que comparações funcionem mesmo com diferenças de maiúsculas/minúsculas ou espaços
            sensores_docas_todos = set()
            for lista_docas in self.sensores_doca_por_estudo.values():
                for s in lista_docas:
                    sensores_docas_todos.add(normalizar_texto(s))
                    
            # Separar sensores de docas vs outros sensores usando chaves normalizadas
            sensores_docas_validos = []
            sensores_gerais = []
            
            for s in sensores_consolidados.keys():
                if normalizar_texto(s) in sensores_docas_todos:
                    sensores_docas_validos.append(s)
                else:
                    sensores_gerais.append(s)
            
            # 1. PONTO QUENTE DE DOCA
            if sensores_docas_validos:
                max_doca_val = max([sensores_consolidados[s]['max'] for s in sensores_docas_validos])
                candidatos_doca = []
                for s in sensores_docas_validos:
                    if sensores_consolidados[s]['max'] == max_doca_val:
                        candidatos_doca.append((s, sensores_consolidados[s]['media']))
                candidatos_doca.sort(key=lambda x: x[1], reverse=True)
                ponto_quente_doca = candidatos_doca[0][0]
            else:
                ponto_quente_doca = "Nenhum sensor de doca identificado"
                
            # 2. OUTROS 2 PONTOS QUENTES GERAIS (excluindo TODOS os sensores de doca)
            # Pontos Quentes Gerais 1 e 2 consideram apenas sensores que NÃO são doca
            candidatos_gerais_quentes = []
            for s, dados in sensores_consolidados.items():
                # Excluir todos os sensores de doca (não apenas o ponto quente de doca)
                if normalizar_texto(s) not in sensores_docas_todos:
                    candidatos_gerais_quentes.append((s, dados['max'], dados['media']))
            # Ordenar por máxima descrescente, depois por média descrescente
            candidatos_gerais_quentes.sort(key=lambda x: (x[1], x[2]), reverse=True)
            
            pontos_quentes_gerais = []
            if len(candidatos_gerais_quentes) >= 1:
                pontos_quentes_gerais.append(candidatos_gerais_quentes[0][0])
            if len(candidatos_gerais_quentes) >= 2:
                pontos_quentes_gerais.append(candidatos_gerais_quentes[1][0])
                
            # 1 PONTO FRIO GERAL (desempate pela média)
            candidatos_frios = []
            for s, dados in sensores_consolidados.items():
                candidatos_frios.append((s, dados['min'], dados['media']))
            # Ordenar por mínima crescente, depois por média crescente
            candidatos_frios.sort(key=lambda x: (x[1], x[2]), reverse=False)
            ponto_frio = candidatos_frios[0][0]
            
            # 1 PONTO DE UMIDADE (se houver dados de umidade, senão indicar)
            ponto_umidade = "Não se aplica (sem dados de umidade)"
            # Se houver dados de umidade consolidados
            estatisticas_umidade = {}
            for df_filtrado, arquivo_idx in dfs_normais:
                sensores_dict_u = self.sensores_por_arquivo[arquivo_idx]['umidade']
                if sensores_dict_u:
                    sensor_externo = self.sensor_externo_por_arquivo.get(arquivo_idx, "")
                    sensores_internos_u = {s: col for s, col in sensores_dict_u.items() if s != sensor_externo}
                    for s, col in sensores_internos_u.items():
                        if s not in estatisticas_umidade:
                            estatisticas_umidade[s] = []
                        valores = df_filtrado[col].dropna()
                        if len(valores) > 0:
                            estatisticas_umidade[s].extend(valores.tolist())
            if estatisticas_umidade:
                # Vamos pegar o sensor de umidade com a maior máxima absoluta
                max_umid_global = -1
                ponto_umid_candidatos = []
                for s, vals in estatisticas_umidade.items():
                    if len(vals) > 0:  # Verificar se ha valores antes de chamar max()
                        m_val = max(vals)
                        med_val = np.mean(vals)
                        if m_val > max_umid_global:
                            max_umid_global = m_val
                            ponto_umid_candidatos = [(s, m_val, med_val)]
                        elif m_val == max_umid_global:
                            ponto_umid_candidatos.append((s, m_val, med_val))
                if ponto_umid_candidatos:  # Verificar se ha candidatos
                    ponto_umid_candidatos.sort(key=lambda x: x[2], reverse=True)
                    ponto_umidade = ponto_umid_candidatos[0][0]
                    ponto_umidade_str = f"Ponto Crítico de Umidade: <b>{ponto_umidade}</b> com máxima de {max_umid_global:.1f}% (Média: {ponto_umid_candidatos[0][2]:.1f}%)"
                else:
                    ponto_umidade_str = "Ponto Crítico de Umidade: Não aplicável (sem dados válidos de umidade)"
            else:
                ponto_umidade_str = "Ponto Crítico de Umidade: Não aplicável (sem monitoramento de umidade)"
                
            # Formatar strings (cores serão aplicadas via sombreamento na tabela consolidada)
            pq_doca_val = sensores_consolidados[ponto_quente_doca]['max'] if ponto_quente_doca in sensores_consolidados else 0
            pq_doca_med = sensores_consolidados[ponto_quente_doca]['media'] if ponto_quente_doca in sensores_consolidados else 0
            p_quente_doca_str = f"Ponto Quente de Doca: <b>{ponto_quente_doca}</b> com máxima de {pq_doca_val:.1f}°C (Média: {pq_doca_med:.1f}°C)"
            
            pq_gerais_str_list = []
            for idx_g, pq_g in enumerate(pontos_quentes_gerais, 1):
                val_g = sensores_consolidados[pq_g]['max']
                med_g = sensores_consolidados[pq_g]['media']
                pq_gerais_str_list.append(f"Ponto Quente Geral {idx_g}: <b>{pq_g}</b> com máxima de {val_g:.1f}°C (Média: {med_g:.1f}°C)")
                
            ponto_frio_str = f"Ponto Frio Geral: <b>{ponto_frio}</b> com mínima de {sensores_consolidados[ponto_frio]['min']:.1f}°C (Média: {sensores_consolidados[ponto_frio]['media']:.1f}°C)"
            
            # Aplicar sombreamento na tabela consolidada para pontos críticos do galpão
            self._aplicar_sombreamento_pontos_criticos(estilo, sensores_ordenados, sensores_consolidados, 
                                                       ponto_quente_doca, pontos_quentes_gerais, ponto_frio, 
                                                       ponto_umidade, self.tipo_equipamento)
            
            # Aplicar estilo à tabela agora que temos os pontos críticos
            tabela_cons.setStyle(TableStyle(estilo))
            
            # Texto explicativo de TEMPERATURA
            texto_temp_analise = Paragraph(
                f"Os pontos críticos foram identificados através da análise consolidada de todos os testes mandatórios, considerando as máximas e mínimas temperaturas registradas em cada sensor. O ponto quente de doca foi definido como o sensor de doca com a maior temperatura máxima, com desempate pela maior temperatura média. Os pontos quentes gerais foram identificados como os dois sensores com as maiores temperaturas máximas entre os sensores não classificados como doca, com desempate pela maior temperatura média. O ponto frio geral foi definido como o sensor com a menor temperatura mínima, com desempate pela menor temperatura média.<br/><br/>"
                f"• {p_quente_doca_str}<br/>"
                f"• {pq_gerais_str_list[0] if len(pq_gerais_str_list) > 0 else ''}<br/>"
                f"• {pq_gerais_str_list[1] if len(pq_gerais_str_list) > 1 else ''}<br/>"
                f"• {ponto_frio_str}",
                ParagraphStyle('AnaliseP3Galpao', parent=styles['Normal'], fontSize=10, leading=15, alignment=TA_LEFT)
            )
            elements.append(texto_temp_analise)
            elements.append(Spacer(1, 5*mm))
            
            # TABELA DE UMIDADE (apenas para galpão)
            if self.tipo_equipamento == "galpao" and umidade_consolidada:
                # Preparar dados para tabela de umidade
                header_u = [RotatedText('UMIDADE', font_size=5)]
                for s in sensores_ordenados:
                    if s in umidade_consolidada:
                        header_u.append(RotatedText(s, font_size=5))
                header_u.extend([RotatedText('MÁXIMA', font_size=5), RotatedText('MÍNIMA', font_size=5), RotatedText('MÉDIA', font_size=5)])
                
                maximas_u = ['MÁXIMA']
                minimas_u = ['MÍNIMA']
                medias_u = ['MÉDIA']
                
                for s in sensores_ordenados:
                    if s in umidade_consolidada:
                        maximas_u.append(f"{umidade_consolidada[s]['max']:.1f}")
                        minimas_u.append(f"{umidade_consolidada[s]['min']:.1f}")
                        medias_u.append(f"{umidade_consolidada[s]['media']:.1f}")
                
                # Aplicar dropna() para valores válidos de umidade
                valores_max_u = [umidade_consolidada[s]['max'] for s in umidade_consolidada if not np.isnan(umidade_consolidada[s]['max'])]
                valores_min_u = [umidade_consolidada[s]['min'] for s in umidade_consolidada if not np.isnan(umidade_consolidada[s]['min'])]
                valores_media_u = [umidade_consolidada[s]['media'] for s in umidade_consolidada if not np.isnan(umidade_consolidada[s]['media'])]
                
                max_global_u = max(valores_max_u) if valores_max_u else 0
                min_global_u = min(valores_min_u) if valores_min_u else 0
                media_global_u = np.mean(valores_media_u) if valores_media_u else 0
                
                maximas_u.extend([f"{max_global_u:.1f}", f"{min_global_u:.1f}", f"{media_global_u:.1f}"])
                minimas_u.extend([f"{max_global_u:.1f}", f"{min_global_u:.1f}", f"{media_global_u:.1f}"])
                medias_u.extend([f"{max_global_u:.1f}", f"{min_global_u:.1f}", f"{media_global_u:.1f}"])
                
                tabela_dados_u = [header_u, maximas_u, minimas_u, medias_u]
                col_widths_u = calcular_larguras_resumo(len([s for s in sensores_ordenados if s in umidade_consolidada]), tem_sensor_externo=False)
                tabela_umid = Table(tabela_dados_u, colWidths=col_widths_u)
                
                estilo_u = [
                    ('GRID', (0, 0), (-1, -1), 0.3, colors.black),
                    ('FONTSIZE', (0, 0), (-1, 0), 5),
                    ('FONTSIZE', (0, 1), (-1, -1), 4),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('BACKGROUND', (-3, 0), (-1, -1), colors.HexColor('#ADD8E6')),
                    ('LEFTPADDING', (0, 0), (-1, -1), 2),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 2),
                    ('TOPPADDING', (0, 0), (-1, -1), 2),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                ]
                
                # Aplicar sombreamento para ponto crítico de umidade (máxima)
                for idx_s, s in enumerate(sensores_ordenados, 1):
                    if s in umidade_consolidada and s == ponto_umidade:
                        estilo_u.append(('BACKGROUND', (idx_s, 1), (idx_s, 1), colors.HexColor('#FF6B6B')))  # Vermelho para máxima
                
                tabela_umid.setStyle(TableStyle(estilo_u))
                elements.append(Paragraph("<b>Tabela Consolidada de Umidade:</b>", ParagraphStyle('TabConsT', parent=styles['Normal'], fontSize=9, spaceAfter=3)))
                elements.append(tabela_umid)
                elements.append(Spacer(1, 5*mm))
                
                # Texto explicativo de UMIDADE
                texto_umid_analise = Paragraph(
                    f"Os dados de umidade foram coletados através dos testes mandatórios e consolidados por sensor. O ponto crítico de umidade foi identificado como o sensor com a maior umidade máxima, com desempate pela maior umidade média.<br/><br/>"
                    f"• {ponto_umidade_str}",
                    ParagraphStyle('AnaliseP3Umid', parent=styles['Normal'], fontSize=10, leading=15, alignment=TA_LEFT)
                )
                elements.append(texto_umid_analise)
            
            
        return elements

    def _criar_capa(self, styles):
        """Cria página de capa - Anexos do Relatório de Qualificação"""
        elements = []
        
        # Título principal
        titulo = Paragraph(
            "<b>ANEXOS DO RELATÓRIO DE QUALIFICAÇÃO TÉRMICA</b>",
            ParagraphStyle('TituloCapa', parent=styles['Heading1'],
                          fontSize=16, alignment=TA_CENTER, spaceAfter=5)
        )
        elements.append(titulo)
        
       
        # Informações principais
        info_text = f"<b>Empresa:</b> {self.empresa}<br/>" \
                    f"<b>Área/Equipamento:</b> {self.area}<br/>" \
                    f"<b>TAG:</b> {self.tag}<br/>" \
                    f"<b>Critério de Temperatura:</b> {self.limite_min_temp}°C a {self.limite_max_temp}°C"
        if self.tratar_umidade:
            info_text += f"<br/><b>Critério de Umidade:</b> {self.limite_min_umidade}% a {self.limite_max_umidade}%"
        
        info = Paragraph(
            info_text,
            ParagraphStyle('InfoCapa', parent=styles['Normal'],
                          fontSize=11, alignment=TA_CENTER, spaceAfter=20)
        )
        elements.append(info)
        
        elements.append(Spacer(1, 20*mm))
        
        # Conteúdo dos anexos
        conteudo_anexos = Paragraph(
            "<b>Conteúdo:</b><br/>"
            "Anexo 1 - Resumo dos estudos<br/>"
            "Anexo 2 - Resumo dos resultados e conclusões<br/>"
            "Anexo 3 - Pontos Críticos identificados<br/>"
            "Anexo 4 - Análises, Gráficos e Dados Brutos",
            ParagraphStyle('ConteudoCapa', parent=styles['Normal'],
                          fontSize=10, alignment=TA_CENTER, spaceAfter=10)
        )
        elements.append(conteudo_anexos)
        
        return elements
    
    def _criar_indice(self, styles):
        """Cria página de índice (Anexo 4)"""
        elements = []
        
        # ANEXO 4 - CABEÇALHO
        titulo_anexo = Paragraph(
            "<b>Anexo 4 - Análises, Gráficos e Dados Brutos</b>",
            ParagraphStyle('TituloAnexo4', parent=styles['Heading1'],
                          fontSize=11, alignment=TA_LEFT, spaceAfter=2)
        )
        elements.append(titulo_anexo)
        
        # Segunda linha do cabeçalho com informações (quebrada em duas linhas)
        info_header = Paragraph(
            f"<b>Área/Equipamento:</b> {self.area} | <b>TAG:</b> {self.tag} | <b>Estudo:</b> Consolidado<br/>"
            f"<b>Tipo:</b> Temperatura | <b>Critério de temperatura:</b> {self.limite_min_temp}°C a {self.limite_max_temp}°C",
            ParagraphStyle('InfoHeader4', parent=styles['Normal'],
                          fontSize=8, alignment=TA_LEFT, spaceAfter=8)
        )
        elements.append(info_header)
        
        indice_data = [['Nº', 'Estudo', 'Duração', 'Tipo', 'Páginas']]
        for idx, estudo in enumerate(self.estudos, 1):
            paginas_tuple = self.paginas_estudos.get(idx, ('?', '?'))
            if isinstance(paginas_tuple, tuple):
                pagina_str = f"{paginas_tuple[0]} - {paginas_tuple[1]}"
            else:
                pagina_str = str(paginas_tuple)
            
            # Formatar duração usando formatar_duracao_br
            unidade = estudo.get('unidade_tempo', 'H')
            if unidade == 'M':
                duracao_min = int(estudo.get('duracao_minutos', 0))
                duracao_str = formatar_duracao_br(0, duracao_min)
            else:
                duracao_h = estudo.get('duracao_horas', 0)
                horas_int = int(duracao_h)
                minutos_int = int((duracao_h - horas_int) * 60)
                duracao_str = formatar_duracao_br(horas_int, minutos_int)
            
            # Indicar se é informativo ou mandatório
            tipo_teste = "Informativo" if estudo.get('teste_informativo', False) else "Mandatório"
            
            indice_data.append([str(idx), estudo['nome'], duracao_str, tipo_teste, pagina_str])
        
        # Larguras das colunas do índice
        col_widths_indice = [15*mm, 150*mm, 30*mm, 40*mm, 35*mm]
        tabela_indice = Table(indice_data, colWidths=col_widths_indice)
        
        # Calcular fontes responsivas para cada coluna
        font_sizes_indice = [calcular_fonte_responsiva(w/mm, min_font=7, max_font=10) for w in col_widths_indice]
        # Aplicar estilos com fonte responsiva
        estilo_indice = [
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),
            ('ALIGN', (1, 0), (1, -1), 'LEFT'),
            ('ALIGN', (2, 0), (4, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]
        
        # Aplicar fonte responsiva para cabeçalho
        estilo_indice.append(('FONTSIZE', (0, 0), (-1, 0), font_sizes_indice[0]))
        
        # Aplicar fonte responsiva para dados (uma por coluna)
        for col_idx, font_size in enumerate(font_sizes_indice):
            estilo_indice.append(('FONTSIZE', (col_idx, 1), (col_idx, -1), font_size))
        
        tabela_indice.setStyle(TableStyle(estilo_indice))
        
        elements.append(tabela_indice)
        
        return elements
    
    def _criar_pagina_grafico_resumo_estudo(self, styles, estudo, df_estudo, arquivo_idx):
        """Cria página com gráfico e resumo do estudo"""
        elements = []
        
        # Obter sensor externo deste arquivo
        sensor_externo = self.sensor_externo_por_arquivo.get(arquivo_idx, "")
        
        if estudo['tipo'] == 'temperatura':
            sensores_dict = self.sensores_por_arquivo[arquivo_idx]['temp']
            limite_min = self.limite_min_temp
            limite_max = self.limite_max_temp
        else:
            sensores_dict = self.sensores_por_arquivo[arquivo_idx]['umidade']
            limite_min = self.limite_min_umidade
            limite_max = self.limite_max_umidade
        
        # Reordenar sensores numericamente
        sensores_ordenados = ordenar_sensores_numericamente(sensores_dict, sensor_externo)
        
        # CABEÇALHO PADRONIZADO
        tipo_label = "TEMPERATURA" if estudo['tipo'] == 'temperatura' else "UMIDADE"
        titulo_empresa = Paragraph(
            f"<b>{self.empresa}</b>",
            ParagraphStyle('TituloEmpresa', parent=styles['Heading1'],
                          fontSize=11, alignment=TA_LEFT, spaceAfter=2)
        )
        elements.append(titulo_empresa)
        
        info = Paragraph(
            f"<b>Anexo 4 - Análises, Gráficos e Dados Brutos</b><br/>"
            f"Área: {self.area} | TAG: {self.tag} | Estudo: {estudo['nome']} | Tipo: {tipo_label}<br/>"
            f"Critério de temperatura: {limite_min}°C a {limite_max}°C",
            ParagraphStyle('Info', parent=styles['Normal'],
                          fontSize=8, alignment=TA_LEFT, spaceAfter=5)
        )
        elements.append(info)
        
        # Criar tabela de resumo
        # Coletar colunas de sensores (excluindo sensor externo para cálculos)
        colunas_sensor = [col for sensor, col in sensores_ordenados.items()]
        colunas_sensor_calc = [col for sensor, col in sensores_ordenados.items() 
                              if sensor != sensor_externo]
        nomes_sensores_calc = [sensor for sensor in sensores_ordenados.keys() 
                              if sensor != sensor_externo]
        
        # Preparar dados de resumo com TAGs rotacionadas
        header = [RotatedText('ESTATÍSTICA', font_size=5)]
        for sensor_nome in sensores_ordenados.keys():
            header.append(RotatedText(sensor_nome, font_size=5))
        header.extend([RotatedText('MÁXIMO', font_size=5), RotatedText('MÍNIMO', font_size=5), RotatedText('MÉDIA', font_size=5)])
        
        # Máximas: exibir todos, mas calcular apenas internos
        maximas = ['MÁXIMA']
        maximas_sensores = []
        for col in colunas_sensor:
            valor_max = df_estudo[col].max()
            maximas.append(f"{valor_max:.1f}" if pd.notna(valor_max) else "ND")
            if col in colunas_sensor_calc:
                if pd.notna(valor_max):
                    maximas_sensores.append(valor_max)
        
        if maximas_sensores:
            maximas.extend([
                f"{max(maximas_sensores):.1f}",
                f"{min(maximas_sensores):.1f}",
                f"{np.mean(maximas_sensores):.1f}"
            ])
        else:
            maximas.extend(['0.0', '0.0', '0.0'])
        
        # Mínimas: exibir todos, mas calcular apenas internos
        minimas = ['MÍNIMA']
        minimas_sensores = []
        for col in colunas_sensor:
            valor_min = df_estudo[col].min()
            minimas.append(f"{valor_min:.1f}" if pd.notna(valor_min) else "ND")
            if col in colunas_sensor_calc:
                if pd.notna(valor_min):
                    minimas_sensores.append(valor_min)
        
        if minimas_sensores:
            minimas.extend([
                f"{max(minimas_sensores):.1f}",
                f"{min(minimas_sensores):.1f}",
                f"{np.mean(minimas_sensores):.1f}"
            ])
        else:
            minimas.extend(['0.0', '0.0', '0.0'])
        
        # Médias: exibir todos, mas calcular apenas internos
        medias = ['MÉDIA']
        medias_sensores = []
        for col in colunas_sensor:
            valor_media = df_estudo[col].mean()
            medias.append(f"{valor_media:.1f}" if pd.notna(valor_media) else "ND")
            if col in colunas_sensor_calc:
                if pd.notna(valor_media):
                    medias_sensores.append(valor_media)
        
        if medias_sensores:
            medias.extend([
                f"{max(medias_sensores):.1f}",
                f"{min(medias_sensores):.1f}",
                f"{np.mean(medias_sensores):.1f}"
            ])
        else:
            medias.extend(['0.0', '0.0', '0.0'])
        
        data_resumo = [header, maximas, minimas, medias]
        
        # Identificar índice do sensor externo
        idx_sensor_externo = None
        if sensor_externo and sensor_externo in sensores_ordenados:
            idx_sensor_externo = list(sensores_ordenados.keys()).index(sensor_externo) + 1
        
        # Identificar máximas e mínimas apenas entre sensores internos
        if maximas_sensores:
            max_valor = max(maximas_sensores)
            idx_max = []
            for nome in nomes_sensores_calc:
                if df_estudo[sensores_ordenados[nome]].max() == max_valor:
                    idx_na_lista = list(sensores_ordenados.keys()).index(nome) + 1
                    idx_max.append(idx_na_lista)
        else:
            idx_max = []
        
        if minimas_sensores:
            min_valor = min(minimas_sensores)
            idx_min = []
            for nome in nomes_sensores_calc:
                if df_estudo[sensores_ordenados[nome]].min() == min_valor:
                    idx_na_lista = list(sensores_ordenados.keys()).index(nome) + 1
                    idx_min.append(idx_na_lista)
        else:
            idx_min = []
        
        # Cálculo responsivo das larguras de coluna (MESMA LÓGICA DOS DADOS BRUTOS)
        larguras = calcular_larguras_resumo(len(sensores_ordenados), tem_sensor_externo=False)
        
        # Definir alturas das linhas para tabela de resumo com tags rotacionados
        altura_header_resumo = 25*mm
        altura_linha_resumo = 7*mm
        rowHeights_resumo = [altura_header_resumo, altura_linha_resumo, altura_linha_resumo, altura_linha_resumo]
        
        # Adicionar título da seção de análise
        analise_titulo = Paragraph(
            "<b>ANÁLISE DOS RESULTADOS:</b>",
            ParagraphStyle('AnaliseT', parent=styles['Heading3'],
                          fontSize=10, spaceAfter=5)
        )
        elements.append(analise_titulo)
        
        # Criar tabela de análise com mesmas larguras dos dados brutos
        tabela = Table(data_resumo, colWidths=larguras, rowHeights=rowHeights_resumo)
        
        # Calcular fontes responsivas para cada coluna
        font_sizes_resumo = [calcular_fonte_responsiva(w/mm, min_font=5, max_font=9) for w in larguras]
        
        estilo = gerar_estilo_tabela_resumo(len(sensores_dict), idx_max, idx_min, idx_sensor_externo)
        
        # Adicionar fontes responsivas ao estilo
        for col_idx, font_size in enumerate(font_sizes_resumo):
            estilo.append(('FONTSIZE', (col_idx, 0), (col_idx, -1), font_size))
        
        tabela.setStyle(TableStyle(estilo))
        
        elements.append(tabela)
        elements.append(Spacer(1, 8*mm))
        
        # Análise crítica em texto (excluindo sensor externo)
        if colunas_sensor_calc:
            # Calcular máximo e mínimo excluindo NaN
            df_calc_limpo = df_estudo[colunas_sensor_calc].dropna(how='all')
            if len(df_calc_limpo) > 0 and df_calc_limpo.notna().any().any():
                sensor_max = df_calc_limpo.max().idxmax()
                temp_max = df_calc_limpo.max().max()
                sensor_min = df_calc_limpo.min().idxmin()
                temp_min = df_calc_limpo.min().min()
            else:
                sensor_max = 'N/A'
                temp_max = 0
                sensor_min = 'N/A'
                temp_min = 0
        else:
            sensor_max = 'N/A'
            temp_max = 0
            sensor_min = 'N/A'
            temp_min = 0
        
        # Encontrar nome base do sensor
        for nome_base, col_original in sensores_dict.items():
            if col_original == sensor_max:
                sensor_max = nome_base
            if col_original == sensor_min:
                sensor_min = nome_base
        
        # Calcular percentual de disponibilidade de dados
        total_dados_esperados = len(df_estudo) * len(sensores_dict)
        dados_disponiveis = 0
        for col in sensores_dict.values():
            dados_disponiveis += df_estudo[col].notna().sum()
        
        percentual_disponibilidade = (dados_disponiveis / total_dados_esperados * 100) if total_dados_esperados > 0 else 0
        
        # Se o teste é informativo, apenas informar resultados sem aplicar critérios
        if estudo.get('teste_informativo', False):
            atende = True  # Teste informativo sempre passa
            status_cor = 'blue'
            status_texto = 'INFORMATIVO (Sem Critério)'
        else:
            atende = (temp_max <= limite_max) and (temp_min >= limite_min)
            status_cor = 'green' if atende else 'red'
            status_texto = 'ATENDIDO' if atende else 'NÃO ATENDIDO'
        
        # Validar disponibilidade mínima de 80% (sempre normal, mesmo para informativos)
        disponibilidade_ok = percentual_disponibilidade >= 80
        status_disponibilidade = 'ATENDIDA' if disponibilidade_ok else 'NÃO ATENDIDA'
        status_disponibilidade_cor = 'green' if disponibilidade_ok else 'red'
        
        unidade = '°C' if estudo['tipo'] == 'temperatura' else '%'
        analise_texto = f"""
• MÁXIMO: Sensor {sensor_max} ({temp_max:.1f}{unidade})<br/>
• MÍNIMO: Sensor {sensor_min} ({temp_min:.1f}{unidade})<br/>
• <font color="{status_cor}"><b>CRITÉRIO: {status_texto}</b></font><br/>
• VARIAÇÃO: {temp_max - temp_min:.1f}{unidade}<br/>
• <font color="{status_disponibilidade_cor}"><b>DISPONIBILIDADE DE DADOS: {status_disponibilidade} ({percentual_disponibilidade:.1f}%)</b></font>
"""
        
        analise_p = Paragraph(analise_texto, styles['Normal'])
        elements.append(analise_p)
        elements.append(Spacer(1, 8*mm))
        
        # GRÁFICO ABAIXO DA ANÁLISE
        titulo_grafico = Paragraph(
            "<b>GRÁFICO:</b>",
            ParagraphStyle('GraficoT', parent=styles['Heading3'],
                          fontSize=10, spaceAfter=5)
        )
        elements.append(titulo_grafico)
        
        # Gerar gráfico
        buffer = self.gerar_grafico_estudo(df_estudo, estudo['nome'], estudo['tipo'], arquivo_idx)
        # Aumentada a altura da imagem no PDF de 110mm para 150mm
        img = Image(buffer, width=280*mm, height=150*mm)
        
        # Centralizar gráfico
        tabela_grafico = Table([
            [img]
        ], colWidths=[270*mm])
        
        tabela_grafico.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        
        elements.append(tabela_grafico)
        
        return elements
    
    def _criar_paginas_dados_estudo(self, styles, estudo, df_estudo, arquivo_idx):
        """Cria páginas de dados brutos"""
        elements = []
        
        # Obter sensor externo deste arquivo
        sensor_externo = self.sensor_externo_por_arquivo.get(arquivo_idx, "")
        
        df_tabela = df_estudo.copy()
        col_data = self.col_data_por_arquivo[arquivo_idx]
        col_hora = self.col_hora_por_arquivo[arquivo_idx]
        
        # Formatar DATA em DD/MM/AAAA e HORA em 00:00
        df_tabela['DATA'] = df_tabela[col_data].apply(formatar_data_br)
        df_tabela['HORA'] = df_tabela[col_hora].apply(formatar_hora_br)
        
        if estudo['tipo'] == 'temperatura':
            sensores_dict = self.sensores_por_arquivo[arquivo_idx]['temp']
            colunas_calc = ['TEMP_MAXIMA', 'TEMP_MINIMA', 'TEMP_MEDIA']
            label_calc = ['TEMP. MÁXIMA', 'TEMP. MÍNIMA', 'TEMP. MÉDIA']
        else:
            sensores_dict = self.sensores_por_arquivo[arquivo_idx]['umidade']
            colunas_calc = ['UMIDADE_MAXIMA', 'UMIDADE_MINIMA', 'UMIDADE_MEDIA']
            label_calc = ['UMIDADE MÁXIMA', 'UMIDADE MÍNIMA', 'UMIDADE MÉDIA']
        
        # Reordenar sensores numericamente
        sensores_ordenados = ordenar_sensores_numericamente(sensores_dict, sensor_externo)
        
        header_row = ['DATA', 'HORA']
        for sensor_nome in sensores_ordenados.keys():
            header_row.append(RotatedText(sensor_nome, font_size=5))
        header_row.extend([RotatedText(label, font_size=5) for label in label_calc])
        
        larguras = calcular_larguras_colunas(len(sensores_dict), tem_sensor_externo=False)
        
        # Ajustar paginação para aproveitar melhor a altura útil da página
        # Usar 37 registros por página para caber tudo numa página
        registros_por_pagina = 17
        total_paginas = (len(df_tabela) + registros_por_pagina - 1) // registros_por_pagina
        
        for pagina in range(total_paginas):
            inicio = pagina * registros_por_pagina
            fim = min(inicio + registros_por_pagina, len(df_tabela))
            df_pagina = df_tabela.iloc[inicio:fim]
            
            if pagina > 0:
                elements.append(PageBreak())
            
            # CABEÇALHO PADRONIZADO
            tipo_label = "TEMPERATURA" if estudo['tipo'] == 'temperatura' else "UMIDADE"
            titulo_empresa = Paragraph(
                f"<b>{self.empresa}</b>",
                ParagraphStyle('TituloEmpresa', parent=styles['Heading1'],
                              fontSize=11, alignment=TA_LEFT, spaceAfter=2)
            )
            elements.append(titulo_empresa)
            
            info = Paragraph(
                f"<b>Anexo 4 - Análises, Gráficos e Dados Brutos</b><br/>"
                f"Área: {self.area} | TAG: {self.tag} | Estudo: {estudo['nome']} | Tipo: {tipo_label}<br/>"
                f"Critério de temperatura: {self.limite_min_temp}°C a {self.limite_max_temp}°C",
                ParagraphStyle('Info', parent=styles['Normal'],
                              fontSize=8, alignment=TA_LEFT, spaceAfter=5)
            )
            elements.append(info)
            
            data_tabela = [header_row]
            
            for _, row in df_pagina.iterrows():
                linha = [row['DATA'], row['HORA']]
                
                for sensor_nome, col_original in sensores_ordenados.items():
                    valor = row[col_original]
                    linha.append(f"{valor:.1f}" if pd.notna(valor) else "ND")
                
                for col_calc in colunas_calc:
                    valor = row[col_calc]
                    linha.append(f"{valor:.1f}" if pd.notna(valor) else "ND")
                
                data_tabela.append(linha)
            
            # Altura padronizada para melhor aproveitamento de espaço
            # Cálculo: 277mm / 17 linhas = 7.5mm por linha
            altura_header = 20*mm
            altura_linha = 7.5*mm  # Permite padding de 4pt em cima e embaixo com espaço para o texto
            rowHeights = [altura_header] + [altura_linha] * len(df_pagina)
            
            # Garantir que as alturas são fixas e padronizadas
            # Isso evita páginas quase vazias e aproveita melhor o espaço
            
            # Usar repeatRows=1 para repetir o cabeçalho em cada página
            tabela = Table(data_tabela, colWidths=larguras, rowHeights=rowHeights, repeatRows=1)
            
            # Calcular fontes responsivas para cada coluna
            font_sizes_dados = [calcular_fonte_responsiva(w/mm, min_font=4, max_font=6) for w in larguras]
            
            estilo = gerar_estilo_tabela_dados()
            
            # Adicionar fontes responsivas ao estilo
            for col_idx, font_size in enumerate(font_sizes_dados):
                estilo.append(('FONTSIZE', (col_idx, 0), (col_idx, -1), font_size))
            
            # Adicionar destaque para sensor externo em cinza
            if sensor_externo and sensor_externo in sensores_ordenados:
                idx_sensor_externo = list(sensores_ordenados.keys()).index(sensor_externo) + 2  # +2 por DATA e HORA
                estilo.append(('BACKGROUND', (idx_sensor_externo, 0), (idx_sensor_externo, -1), 
                             colors.HexColor('#D3D3D3')))
            
            # Nota: ALIGN, VALIGN e padding já estão definidos em gerar_estilo_tabela_dados()
            # Não replicar para evitar conflitos
            
            # Adicionar destaque para cálculos em azul claro
            idx_primeira_calc = len(sensores_ordenados) + 2  # +2 por DATA e HORA
            estilo.append(('BACKGROUND', (idx_primeira_calc, 1), (-1, -1), 
                         colors.HexColor('#ADD8E6')))
            
            tabela.setStyle(TableStyle(estilo))
            elements.append(tabela)
        
        return elements
    
    def _cabecalho_pagina(self, canvas_obj, doc):
        """Adiciona cabeçalho em páginas posteriores"""
        # Este método pode ser usado para adicionar cabeçalho em páginas posteriores se necessário
        pass
    
    def _rodape(self, canvas_obj, doc):
        """Adiciona rodé com apenas empresa e número de página"""
        canvas_obj.saveState()
        canvas_obj.setFont('Helvetica', 7)
        
        # Usar o número de página do documento
        texto = f"{self.empresa} - Página {doc.page}"
        canvas_obj.drawCentredString(148.5*mm, 10*mm, texto)
        canvas_obj.restoreState()


# ============================================================================
# FUNÇÃO PRINCIPAL
# ============================================================================

def main():
    """Função principal"""
    try:
        gerador = GeradorRelatorioGxP()
        
        if not gerador.selecionar_arquivos():
            input("\nErro ao selecionar arquivos. Pressione ENTER...")
            return
        
        if not gerador.configurar_parametros_gerais():
            input("\nErro na configuração. Pressione ENTER...")
            return
        
        if not gerador.processar_todos_arquivos():
            input("\nErro ao processar arquivos. Pressione ENTER...")
            return
        
        arquivo_pdf = gerador.gerar_pdf()
        
        print("\n" + "=" * 80)
        print("  ✅ RELATÓRIO GERADO COM SUCESSO!")
        print("=" * 80)
        print(f"\nPDF salvo em:")
        print(f"   {arquivo_pdf}")
        print(f"\nLog de auditoria salvo em:")
        print(f"   {gerador.log_path}")
        
    except Exception as e:
        print(f"\nErro: {e}")
        import traceback
        traceback.print_exc()
    
    input("\n✅ Processo concluído! Pressione ENTER...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrompido pelo usuário.")
    except Exception as e:
        print(f"\nErro crítico: {e}")
        import traceback
        traceback.print_exc()
