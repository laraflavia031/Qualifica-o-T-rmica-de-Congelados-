#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
App Streamlit — Interface para o Gerador de Relatórios de Qualificação Térmica
Substitui os inputs do terminal por uma interface gráfica.
A lógica original do script NÃO foi alterada.
"""

import streamlit as st
import tempfile
import os
import sys
import io
import traceback
from pathlib import Path
import pandas as pd

# ─── Importa tudo do script original sem executar o main() ───────────────────
# Evita o bloco print() de inicialização do script original
import importlib.util, types

def _carregar_script_silenciosamente(caminho):
    """Importa o script original suprimindo os prints iniciais."""
    spec = importlib.util.spec_from_file_location("script_original", caminho)
    mod = importlib.util.module_from_spec(spec)
    # Suprimir prints do import
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.stdout = old_stdout
    return mod

# Localiza o script original — tenta variações de nome comuns
def _encontrar_script():
    base = os.path.dirname(os.path.abspath(__file__))
    candidatos = [
        "SCRIPT-ANEXOSCOMPLETOS.py",
        "SCRIPT_ANEXOSCOMPLETOS.py",
        "script-anexoscompletos.py",
        "script_anexoscompletos.py",
    ]
    for nome in candidatos:
        caminho = os.path.join(base, nome)
        if os.path.exists(caminho):
            return caminho
    # Último recurso: qualquer .py na mesma pasta que contenha "SCRIPT" e "ANEXOS"
    for f in os.listdir(base):
        fu = f.upper()
        if f.endswith(".py") and "SCRIPT" in fu and "ANEXO" in fu:
            return os.path.join(base, f)
    raise FileNotFoundError(
        f"Script original não encontrado em '{base}'.\n"
        "Certifique-se de que o arquivo SCRIPT-ANEXOSCOMPLETOS.py está na mesma pasta que app.py."
    )

SCRIPT_PATH = _encontrar_script()
script = _carregar_script_silenciosamente(SCRIPT_PATH)
GeradorRelatorioGxP = script.GeradorRelatorioGxP
calcular_hash_arquivo = script.calcular_hash_arquivo
criar_log_auditoria = script.criar_log_auditoria
log_auditoria = script.log_auditoria
normalizar_texto = script.normalizar_texto

# ─── Configuração da página ───────────────────────────────────────────────────
st.set_page_config(
    page_title="Qualificação Térmica",
    page_icon="🌡️",
    layout="wide",
)

st.markdown("""
<style>
.step-header { font-size: 1.1rem; font-weight: 700; color: #1f4e79; margin-bottom: 4px; }
.step-sub { color: #555; font-size: 0.85rem; margin-bottom: 16px; }
.info-box { background:#e8f4fd; border-left:4px solid #2196F3; padding:10px 14px;
            border-radius:4px; margin-bottom:12px; font-size:0.88rem; }
.success-box { background:#e8f8e8; border-left:4px solid #4CAF50; padding:10px 14px;
               border-radius:4px; margin-bottom:12px; font-size:0.88rem; }
.warn-box { background:#fff8e1; border-left:4px solid #FF9800; padding:10px 14px;
            border-radius:4px; margin-bottom:12px; font-size:0.88rem; }
div[data-testid="stSidebar"] { min-width: 220px; }
</style>
""", unsafe_allow_html=True)

# ─── Estado global ────────────────────────────────────────────────────────────
ETAPAS = ["📁 Arquivos", "⚙️ Parâmetros", "📊 Estudos", "🔬 Sensores", "🚀 Gerar"]

def init_state():
    defaults = {
        "etapa": 0,
        # Arquivos
        "arquivos_info": [],          # [{'caminho', 'hash', 'nome'}]
        "arquivos_tmpdir": None,      # TemporaryDirectory para manter arquivos vivos
        # Parâmetros gerais
        "empresa": "",
        "area": "",
        "tag": "",
        "tipo_equipamento": "refrigerador",
        "modo_equipamento": "conservacao",  # NOVO: conservacao ou maturacao (para freezer/container)
        "incluir_teste_maturacao": False,  # NOVO: se deve incluir teste de maturacao
        "limite_min_temp": 2.0,
        "limite_max_temp": 8.0,
        "tratar_umidade": False,
        "limite_min_umidade": 30.0,
        "limite_max_umidade": 70.0,
        # Estudos
        "num_estudos": 1,
        "estudos_info": [],           # lista de dicts com nome, data_inicio, hora_inicio, etc.
        "gerar_umidade": False,
        # Dados processados (do script original)
        "gerador": None,
        "dados_carregados": False,
        # Sensores (por arquivo)
        "sensores_por_arquivo": {},   # {arquivo_idx: {'temp': [...], 'umidade': [...], 'todas': [...]}}
        "num_sensores_esperados": {},  # {arquivo_idx: int}
        "sensor_externo": {},         # {arquivo_idx: str}
        # Docas (galpão)
        "sensores_doca_por_estudo": {},
        # Resultado
        "pdf_bytes": None,
        "pdf_nome": "",
        "log_path": "",
        "log_bytes": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()

# ─── Sidebar: barra de progresso ─────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🌡️ Qualificação Térmica")
    st.markdown("---")
    for i, nome in enumerate(ETAPAS):
        if i < st.session_state.etapa:
            st.markdown(f"✅ {nome}")
        elif i == st.session_state.etapa:
            st.markdown(f"**▶️ {nome}**")
        else:
            st.markdown(f"○ {nome}")
    st.markdown("---")
    if st.button("🔄 Recomeçar", use_container_width=True):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()

# ─── Helpers ─────────────────────────────────────────────────────────────────
def avancar():
    st.session_state.etapa += 1
    st.rerun()

def voltar():
    st.session_state.etapa -= 1
    st.rerun()

def mostrar_nav(pode_avancar=True, label_avancar="Avançar →"):
    col1, col2 = st.columns([1, 5])
    with col1:
        if st.session_state.etapa > 0:
            if st.button("← Voltar"):
                voltar()
    with col2:
        if pode_avancar:
            if st.button(label_avancar, type="primary"):
                avancar()

# ═══════════════════════════════════════════════════════════════════════════════
# ETAPA 0 — Seleção de Arquivos
# ═══════════════════════════════════════════════════════════════════════════════
if st.session_state.etapa == 0:
    st.title("📁 Seleção de Arquivos")
    st.markdown('<p class="step-sub">Faça upload dos arquivos Excel/CSV com os dados de temperatura (máximo 10 arquivos).</p>', unsafe_allow_html=True)

    uploaded = st.file_uploader(
        "Selecione os arquivos de dados",
        type=["xlsx", "xls", "csv"],
        accept_multiple_files=True,
        help="Arquivos Excel (.xlsx, .xls) ou CSV (.csv) com os dados dos sensores."
    )

    if uploaded:
        st.markdown(f'<div class="info-box">📂 {len(uploaded)} arquivo(s) selecionado(s)</div>', unsafe_allow_html=True)
        for f in uploaded[:10]:
            st.markdown(f"- `{f.name}`")

    pode_avancar = bool(uploaded and 1 <= len(uploaded) <= 10)

    if st.button("Avançar →", type="primary", disabled=not pode_avancar):
        # Salvar arquivos em diretório temporário persistente
        tmpdir = tempfile.mkdtemp()
        st.session_state.arquivos_tmpdir = tmpdir
        arquivos_info = []
        for f in uploaded[:10]:
            caminho = os.path.join(tmpdir, f.name)
            with open(caminho, "wb") as fout:
                fout.write(f.getbuffer())
            h = calcular_hash_arquivo(caminho)
            arquivos_info.append({"caminho": caminho, "hash": h, "nome": f.name})
        st.session_state.arquivos_info = arquivos_info
        st.session_state.dados_carregados = False  # forçar reprocessamento
        avancar()

# ═══════════════════════════════════════════════════════════════════════════════
# ETAPA 1 — Parâmetros Gerais
# ═══════════════════════════════════════════════════════════════════════════════
elif st.session_state.etapa == 1:
    st.title("⚙️ Parâmetros Gerais")
    st.markdown('<p class="step-sub">Configure as informações do relatório e os critérios de aceite.</p>', unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)
    with col1:
        empresa = st.text_input("Nome da Empresa *", value=st.session_state.empresa)
    with col2:
        area = st.text_input("Área / Equipamento *", value=st.session_state.area)
    with col3:
        tag = st.text_input("TAG *", value=st.session_state.tag)

    st.markdown("---")
    st.markdown("**Tipo de Equipamento/Área**")
    tipo_eq = st.radio(
        "Selecione o tipo:",
        options=["refrigerador", "galpao", "freezer", "container"],
        format_func=lambda x: {
            "refrigerador": "Refrigerador ou Camara Fria",
            "galpao": "Galpao",
            "freezer": "Freezer",
            "container": "Container"
        }[x],
        index=["refrigerador", "galpao", "freezer", "container"].index(st.session_state.tipo_equipamento),
        horizontal=True,
    )
    
    # NOVO: Adicionar modo de operacao para freezer/container
    if tipo_eq in ["freezer", "container"]:
        st.markdown("---")
        st.markdown("**Modo de Operacao**")
        modo_eq = st.radio(
            "Selecione o modo:",
            options=["conservacao", "maturacao"],
            format_func=lambda x: "Conservacao (Pass/Fail)" if x == "conservacao" else "Maturacao (Calculo Automatico)",
            index=0 if st.session_state.modo_equipamento == "conservacao" else 1,
            horizontal=True,
        )
        st.session_state.modo_equipamento = modo_eq
    else:
        st.session_state.modo_equipamento = "conservacao"

    st.markdown("---")
    st.markdown("**Critérios de Temperatura (°C)**")
    col_t1, col_t2 = st.columns(2)
    with col_t1:
        min_temp = st.number_input("Temperatura mínima (°C)", value=float(st.session_state.limite_min_temp), step=0.5, format="%.1f")
    with col_t2:
        max_temp = st.number_input("Temperatura máxima (°C)", value=float(st.session_state.limite_max_temp), step=0.5, format="%.1f")

    st.markdown("---")
    tratar_umid = st.checkbox("Tratar umidade relativa?", value=st.session_state.tratar_umidade)
    if tratar_umid:
        st.markdown("**Critérios de Umidade (%)**")
        col_u1, col_u2 = st.columns(2)
        with col_u1:
            min_umid = st.number_input("Umidade mínima (%)", value=float(st.session_state.limite_min_umidade), step=1.0, format="%.1f")
        with col_u2:
            max_umid = st.number_input("Umidade máxima (%)", value=float(st.session_state.limite_max_umidade), step=1.0, format="%.1f")
    else:
        min_umid = st.session_state.limite_min_umidade
        max_umid = st.session_state.limite_max_umidade

    pode_avancar = bool(empresa.strip() and area.strip() and tag.strip() and min_temp < max_temp)
    if not pode_avancar and (empresa or area or tag):
        if min_temp >= max_temp:
            st.warning("⚠️ A temperatura mínima deve ser menor que a máxima.")

    col_nav1, col_nav2 = st.columns([1, 5])
    with col_nav1:
        if st.button("← Voltar"):
            voltar()
    with col_nav2:
        if st.button("Avançar →", type="primary", disabled=not pode_avancar):
            st.session_state.empresa = empresa.strip()
            st.session_state.area = area.strip()
            st.session_state.tag = tag.strip()
            st.session_state.tipo_equipamento = tipo_eq
            st.session_state.limite_min_temp = min_temp
            st.session_state.limite_max_temp = max_temp
            st.session_state.tratar_umidade = tratar_umid
            st.session_state.limite_min_umidade = min_umid
            st.session_state.limite_max_umidade = max_umid
            avancar()

# ═══════════════════════════════════════════════════════════════════════════════
# ETAPA 2 — Definição de Estudos
# ═══════════════════════════════════════════════════════════════════════════════
elif st.session_state.etapa == 2:
    st.title("📊 Definição dos Estudos")
    st.markdown('<p class="step-sub">Informe quantos estudos serão gerados e os detalhes de cada um.</p>', unsafe_allow_html=True)

    num_estudos = st.number_input(
        "Número de estudos *",
        min_value=1, max_value=10,
        value=int(st.session_state.num_estudos),
        step=1,
    )

    estudos_info = []
    for i in range(int(num_estudos)):
        with st.expander(f"📅 Estudo {i+1}", expanded=True):
            default = st.session_state.estudos_info[i] if i < len(st.session_state.estudos_info) else {}
            c1, c2 = st.columns(2)
            with c1:
                nome = st.text_input(f"Nome do estudo", value=default.get("nome", f"ESTUDO {i+1}"), key=f"nome_{i}")
                data_ini = st.text_input(f"Data de início (DD/MM/AAAA)", value=default.get("data_inicio", ""), key=f"data_{i}", placeholder="Ex: 15/01/2024")
                hora_ini = st.text_input(f"Hora de início (HH:MM)", value=default.get("hora_inicio", ""), key=f"hora_{i}", placeholder="Ex: 08:00")
            with c2:
                informativo = st.checkbox("Teste informativo?", value=default.get("teste_informativo", False), key=f"inf_{i}")
                
                # NOVO: Checkbox para indicar se este estudo especifico eh de maturacao
                teste_maturacao = False
                if st.session_state.tipo_equipamento in ["freezer", "container"]:
                    teste_maturacao = st.checkbox("Teste de Maturacao?", value=default.get("teste_maturacao", False), key=f"mat_{i}")
                
                # Se for maturacao, nao pedir duracao
                if teste_maturacao:
                    st.info("Para testes de maturacao, a duracao sera calculada automaticamente.")
                    duracao = 168.0  # Valor padrao (sera ignorado)
                    unidade = "Horas"
                    unid_key = "H"
                else:
                    unidade = st.radio("Duracao em:", ["Horas", "Minutos"], index=0 if default.get("unidade_tempo", "H") == "H" else 1, key=f"unid_{i}", horizontal=True)
                    dur_label = "Duracao (horas)" if unidade == "Horas" else "Duracao (minutos)"
                    dur_val = default.get("duracao_horas", 168.0) if unidade == "Horas" else default.get("duracao_minutos", 168.0 * 60)
                    duracao = st.number_input(dur_label, value=float(dur_val), min_value=0.1, step=1.0, key=f"dur_{i}")
                    
                    if unidade == "Horas":
                        dur_h = duracao
                        dur_m = duracao * 60
                        unid_key = "H"
                    else:
                        dur_m = duracao
                        dur_h = duracao / 60
                        unid_key = "M"

            if teste_maturacao:
                dur_h = 168.0
                dur_m = 168.0 * 60
                unid_key = "H"
            else:
                if unidade == "Horas":
                    dur_h = duracao
                    dur_m = duracao * 60
                    unid_key = "H"
                else:
                    dur_m = duracao
                    dur_h = duracao / 60
                    unid_key = "M"

            estudos_info.append({
                "nome": nome if nome else f"ESTUDO {i+1}",
                "data_inicio": data_ini,
                "hora_inicio": hora_ini,
                "duracao_horas": dur_h,
                "duracao_minutos": dur_m,
                "unidade_tempo": unid_key,
                "teste_informativo": informativo,
                "teste_maturacao": teste_maturacao,  # NOVO: Indicador por estudo
            })

    st.markdown("---")
    gerar_umid = st.checkbox(
        "Gerar também estudos de umidade (se houver dados)?",
        value=st.session_state.gerar_umidade,
    )

    # Validação básica de campos obrigatórios
    campos_ok = all(e["data_inicio"] and e["hora_inicio"] for e in estudos_info)
    if not campos_ok:
        st.warning("⚠️ Preencha a data e hora de início de todos os estudos.")

    col_nav1, col_nav2 = st.columns([1, 5])
    with col_nav1:
        if st.button("← Voltar"):
            voltar()
    with col_nav2:
        if st.button("Avançar →", type="primary", disabled=not campos_ok):
            st.session_state.num_estudos = int(num_estudos)
            st.session_state.estudos_info = estudos_info
            st.session_state.gerar_umidade = gerar_umid
            st.session_state.dados_carregados = False
            avancar()

# ═══════════════════════════════════════════════════════════════════════════════
# ETAPA 3 — Sensores e Configurações por Arquivo
# ═══════════════════════════════════════════════════════════════════════════════
elif st.session_state.etapa == 3:
    st.title("🔬 Sensores e Configurações")
    st.markdown('<p class="step-sub">Configure os sensores de cada arquivo de dados.</p>', unsafe_allow_html=True)

    # ── Carregamento inicial dos arquivos (feito aqui para já ter os sensores) ──
    if not st.session_state.dados_carregados:
        with st.spinner("Carregando arquivos e detectando sensores…"):
            # Garantir que ~/Downloads existe (necessário no Streamlit Cloud)
            downloads_dir = os.path.join(Path.home(), "Downloads")
            os.makedirs(downloads_dir, exist_ok=True)

            gerador = GeradorRelatorioGxP.__new__(GeradorRelatorioGxP)
            # Inicializar atributos manualmente (sem chamar __init__ que tem print)
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                gerador.__init__()
            finally:
                sys.stdout = old_stdout

            # Injetar arquivos diretamente
            gerador.arquivos = list(st.session_state.arquivos_info)

            # Injetar parâmetros gerais
            gerador.empresa = st.session_state.empresa
            gerador.area = st.session_state.area
            gerador.tag = st.session_state.tag
            gerador.tipo_equipamento = st.session_state.tipo_equipamento
            gerador.limite_min_temp = st.session_state.limite_min_temp
            gerador.limite_max_temp = st.session_state.limite_max_temp
            gerador.tratar_umidade = st.session_state.tratar_umidade
            gerador.limite_min_umidade = st.session_state.limite_min_umidade
            gerador.limite_max_umidade = st.session_state.limite_max_umidade

            # Carregar cada arquivo
            sensores_por_arquivo = {}
            erros = []
            for idx, arq in enumerate(gerador.arquivos, 1):
                old_out = sys.stdout
                sys.stdout = io.StringIO()
                try:
                    df = gerador.carregar_dados_arquivo(arq)
                finally:
                    sys.stdout = old_out

                if df is None:
                    erros.append(f"Erro ao carregar '{arq['nome']}'")
                    continue

                # Detectar sensores usando a lógica original do script
                # (copiada de processar_arquivo para não precisar do input de num_sensores ainda)
                sensores_temp = {}
                sensores_umidade = {}
                # Identificar col_data e col_hora
                old_out = sys.stdout
                sys.stdout = io.StringIO()
                try:
                    dados = gerador.processar_arquivo.__func__  # referência sem chamar
                finally:
                    sys.stdout = old_out

                # Usar o método completo mas redirecionar input para stub temporário
                # Como processar_arquivo chama input(), vamos apenas detectar colunas aqui
                for col in df.columns:
                    if col in ['timestamp']:
                        continue
                    if any(x in col for x in ['[°C]', '[ºC]', '[°c]', '[ºc]', '[C]', '[c]', '°C', 'ºC']):
                        nome_base = col
                        for rem in ['[°C]', '[ºC]', '[°c]', '[ºc]', '[C]', '[c]', '°C', 'ºC', ' [°C]', ' °C']:
                            nome_base = nome_base.replace(rem, '').strip()
                        if nome_base:
                            sensores_temp[nome_base] = col
                    elif any(x in col for x in ['[%Hr]', '[%rH]', '[%RH]', '[%hr]', '%Hr', '%rH', '%RH', '%hr']):
                        nome_base = col
                        for rem in ['[%Hr]', '[%rH]', '[%RH]', '[%hr]', '%Hr', '%rH', '%RH', '%hr']:
                            nome_base = nome_base.replace(rem, '').strip()
                        if nome_base:
                            sensores_umidade[nome_base] = col

                # Se não detectou nenhum sensor de temperatura, listar todas as colunas numéricas
                if not sensores_temp:
                    colunas_num = [c for c in df.columns if c not in ['timestamp'] and
                                   pd.api.types.is_numeric_dtype(df[c])]
                    sensores_por_arquivo[idx] = {
                        "temp_auto": {},
                        "umidade_auto": {},
                        "todas_colunas": colunas_num,
                        "detectado_auto": False,
                        "df": df,
                        "nome_arquivo": arq["nome"],
                    }
                else:
                    sensores_por_arquivo[idx] = {
                        "temp_auto": sensores_temp,
                        "umidade_auto": sensores_umidade,
                        "todas_colunas": list(df.columns),
                        "detectado_auto": True,
                        "df": df,
                        "nome_arquivo": arq["nome"],
                    }

            if erros:
                for e in erros:
                    st.error(e)
            else:
                st.session_state.sensores_por_arquivo = sensores_por_arquivo
                st.session_state.gerador_base = gerador
                st.session_state.dados_carregados = True
                st.rerun()

    if st.session_state.dados_carregados:
        sensores_config = {}
        num_sensores_config = {}
        sensor_externo_config = {}

        for idx, info in st.session_state.sensores_por_arquivo.items():
            st.markdown(f"### 📂 Arquivo {idx}: `{info['nome_arquivo']}`")

            if info["detectado_auto"]:
                st.markdown(f'<div class="success-box">✅ {len(info["temp_auto"])} sensor(es) de temperatura detectado(s) automaticamente.</div>', unsafe_allow_html=True)
                sensores_temp_lista = sorted(info["temp_auto"].keys())
            else:
                st.markdown('<div class="warn-box">⚠️ Nenhum sensor detectado automaticamente. Selecione as colunas de temperatura manualmente.</div>', unsafe_allow_html=True)
                colunas = info["todas_colunas"]
                sel = st.multiselect(
                    f"Selecione as colunas de temperatura (arquivo {idx})",
                    options=colunas,
                    key=f"sel_temp_{idx}",
                )
                sensores_temp_lista = sel

            num_sensores_config[idx] = st.number_input(
                f"Com quantos sensores o teste iniciou? (arquivo {idx})",
                min_value=1,
                value=len(sensores_temp_lista) if sensores_temp_lista else 1,
                step=1,
                key=f"num_sens_{idx}",
            )

            opcoes_ext = ["(Nenhum)"] + sensores_temp_lista
            sensor_ext_default = 0
            prev_ext = st.session_state.sensor_externo.get(idx, "")
            if prev_ext in opcoes_ext:
                sensor_ext_default = opcoes_ext.index(prev_ext)

            sensor_ext = st.selectbox(
                f"Sensor externo (será destacado em cinza) — arquivo {idx}",
                options=opcoes_ext,
                index=sensor_ext_default,
                key=f"ext_{idx}",
            )
            sensor_externo_config[idx] = "" if sensor_ext == "(Nenhum)" else sensor_ext
            sensores_config[idx] = sensores_temp_lista

            # Docas (apenas se galpão)
            if st.session_state.tipo_equipamento == "galpao":
                st.markdown("**Sensores de Doca** (apenas para galpão)")
                docas = st.multiselect(
                    f"Selecione os sensores de doca (arquivo {idx})",
                    options=sensores_temp_lista,
                    key=f"docas_{idx}",
                    default=st.session_state.sensores_doca_por_estudo.get(idx, []),
                )
                st.session_state.sensores_doca_por_estudo[idx] = docas

            st.markdown("---")

        pode_avancar = all(bool(v) for v in sensores_config.values())
        if not pode_avancar:
            st.warning("⚠️ Selecione ao menos um sensor de temperatura para cada arquivo.")

        col_nav1, col_nav2 = st.columns([1, 5])
        with col_nav1:
            if st.button("← Voltar"):
                voltar()
        with col_nav2:
            if st.button("Avançar →", type="primary", disabled=not pode_avancar):
                st.session_state.sensores_selecionados = sensores_config
                st.session_state.num_sensores_esperados = num_sensores_config
                st.session_state.sensor_externo = sensor_externo_config
                avancar()

# ═══════════════════════════════════════════════════════════════════════════════
# ETAPA 4 — Geração do PDF
# ═══════════════════════════════════════════════════════════════════════════════
elif st.session_state.etapa == 4:
    st.title("🚀 Geração do Relatório")

    if st.session_state.pdf_bytes:
        st.markdown('<div class="success-box">✅ Relatório já gerado! Faça o download abaixo.</div>', unsafe_allow_html=True)
        st.download_button(
            label="⬇️ Baixar Relatório PDF",
            data=st.session_state.pdf_bytes,
            file_name=st.session_state.pdf_nome,
            mime="application/pdf",
            type="primary",
        )
        if st.session_state.log_bytes:
            st.download_button(
                label="⬇️ Baixar Log de Auditoria",
                data=st.session_state.log_bytes,
                file_name=os.path.basename(st.session_state.log_path),
                mime="text/plain",
            )
        if st.button("← Voltar"):
            voltar()
        st.stop()

    # Resumo antes de gerar
    st.markdown("### Resumo da configuração")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**Empresa:** {st.session_state.empresa}")
        st.markdown(f"**Área:** {st.session_state.area}")
        st.markdown(f"**TAG:** {st.session_state.tag}")
        st.markdown(f"**Tipo:** {'Refrigerador/Câmara Fria' if st.session_state.tipo_equipamento == 'refrigerador' else 'Galpão'}")
    with col2:
        st.markdown(f"**Temperatura:** {st.session_state.limite_min_temp}°C a {st.session_state.limite_max_temp}°C")
        st.markdown(f"**Umidade:** {'Sim' if st.session_state.tratar_umidade else 'Não'}")
        st.markdown(f"**Arquivos:** {len(st.session_state.arquivos_info)}")
        st.markdown(f"**Estudos:** {st.session_state.num_estudos}")

    st.markdown("---")

    col_nav1, col_nav2 = st.columns([1, 5])
    with col_nav1:
        if st.button("← Voltar"):
            voltar()
    with col_nav2:
        gerar = st.button("🚀 Gerar Relatório PDF", type="primary")

    if gerar:
        log_container = st.empty()
        progress_bar = st.progress(0, text="Iniciando…")

        log_lines = []
        def log_ui(msg):
            log_lines.append(msg)
            log_container.code("\n".join(log_lines[-30:]), language=None)

        try:
            log_ui("Inicializando gerador…")
            progress_bar.progress(5, text="Inicializando…")

            # ── Criar instância do gerador ──────────────────────────────────
            # Garantir que ~/Downloads existe (necessário no Streamlit Cloud)
            downloads_dir = os.path.join(Path.home(), "Downloads")
            os.makedirs(downloads_dir, exist_ok=True)

            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                gerador = GeradorRelatorioGxP()
            finally:
                sys.stdout = old_stdout

            # ── Injetar arquivos ────────────────────────────────────────────
            gerador.arquivos = list(st.session_state.arquivos_info)

            # ── Injetar parâmetros gerais ───────────────────────────────────
            gerador.empresa = st.session_state.empresa
            gerador.area = st.session_state.area
            gerador.tag = st.session_state.tag
            gerador.tipo_equipamento = st.session_state.tipo_equipamento
            # NOVO: Injetar parametros de freezer/container
            gerador.modo_equipamento = st.session_state.get("modo_equipamento", "conservacao")
            gerador.incluir_teste_maturacao = st.session_state.get("incluir_teste_maturacao", False)
            gerador.limite_min_temp = st.session_state.limite_min_temp
            gerador.limite_max_temp = st.session_state.limite_max_temp
            gerador.tratar_umidade = st.session_state.tratar_umidade
            gerador.limite_min_umidade = st.session_state.limite_min_umidade
            gerador.limite_max_umidade = st.session_state.limite_max_umidade

            log_ui("Parâmetros gerais injetados.")
            progress_bar.progress(10, text="Processando arquivos…")

            # ── Processar arquivos usando a lógica original, com stub de input ──
            import unittest.mock as mock

            sensores_selecionados = st.session_state.sensores_selecionados
            num_sensores_esperados = st.session_state.num_sensores_esperados
            sensor_externo = st.session_state.sensor_externo
            estudos_info = st.session_state.estudos_info
            gerar_umidade = st.session_state.gerar_umidade

            # Índice de controle para o mock de input por arquivo
            _arquivo_processando = [1]

            def _mock_input_processar(prompt=""):
                idx = _arquivo_processando[0]
                prompt_lower = prompt.lower()

                # Sensor de temperatura manual
                if "sensores de temperatura" in prompt_lower:
                    sens = sensores_selecionados.get(idx, [])
                    sp = st.session_state.sensores_por_arquivo.get(idx, {})
                    todas = sp.get("todas_colunas", [])
                    indices = []
                    for s in sens:
                        if s in todas:
                            indices.append(str(todas.index(s) + 1))
                    return ",".join(indices) if indices else "1"

                # Número de sensores esperados
                if "quantos sensores" in prompt_lower:
                    return str(num_sensores_esperados.get(idx, len(sensores_selecionados.get(idx, []))))

                # Sensor externo
                # O script pergunta: "Escolha o número do sensor" e lista
                # sensores_temp.keys() numerados a partir de 1.
                # Precisamos devolver o índice correto dentro dessa lista.
                if "escolha o número do sensor" in prompt_lower:
                    ext = sensor_externo.get(idx, "")
                    if not ext:
                        return "0"
                    # Recuperar a lista de sensores de temperatura do arquivo
                    # exatamente como o script a monta: sensores_temp.keys()
                    sp = st.session_state.sensores_por_arquivo.get(idx, {})
                    temp_auto = sp.get("temp_auto", {})
                    if temp_auto:
                        sensores_lista = list(temp_auto.keys())
                    else:
                        # fallback: usar lista selecionada manualmente
                        sensores_lista = sensores_selecionados.get(idx, [])
                    ext_norm = ext.strip()
                    for i, s in enumerate(sensores_lista):
                        if s.strip() == ext_norm:
                            return str(i + 1)
                    # correspondência parcial como fallback
                    for i, s in enumerate(sensores_lista):
                        if ext_norm in s.strip() or s.strip() in ext_norm:
                            return str(i + 1)
                    return "0"

                return ""

            # Processar cada arquivo
            arquivos_processados = []
            for idx, arquivo_info in enumerate(gerador.arquivos, 1):
                _arquivo_processando[0] = idx
                log_ui(f"Processando arquivo {idx}: {arquivo_info['nome']}…")

                old_out = sys.stdout
                sys.stdout = io.StringIO()
                try:
                    with mock.patch("builtins.input", side_effect=_mock_input_processar):
                        df = gerador.carregar_dados_arquivo(arquivo_info)
                        if df is None:
                            raise RuntimeError(f"Erro ao carregar arquivo {arquivo_info['nome']}")
                        dados_processados = gerador.processar_arquivo(df, arquivo_info, idx)
                        if dados_processados is None:
                            raise RuntimeError(f"Erro ao processar arquivo {arquivo_info['nome']}")
                finally:
                    sys.stdout = old_out

                gerador.sensores_por_arquivo[idx] = {
                    'temp': dados_processados['sensores_temp'].copy(),
                    'umidade': dados_processados['sensores_umidade'].copy()
                }
                gerador.dados_por_arquivo[idx] = dados_processados['df'].copy()
                gerador.sensor_externo_por_arquivo[idx] = dados_processados['sensor_externo']
                gerador.col_data_por_arquivo[idx] = dados_processados['col_data']
                gerador.col_hora_por_arquivo[idx] = dados_processados['col_hora']
                arquivos_processados.append({'arquivo_info': arquivo_info, 'dados': dados_processados, 'idx': idx})
                log_ui(f"  ✅ Arquivo {idx} processado.")

            progress_bar.progress(40, text="Configurando estudos…")

            # ── Injetar estudos_info ────────────────────────────────────────
            import re as _re

            gerador.estudos_info = []
            for ei in estudos_info:
                gerador.estudos_info.append(dict(ei))

            # Detectar arquivo para cada estudo (lógica original)
            log_ui("Buscando arquivos para cada estudo…")
            for estudo_info in gerador.estudos_info:
                try:
                    data_inicio_dt = pd.to_datetime(
                        f"{estudo_info['data_inicio']} {estudo_info['hora_inicio']}",
                        format='%d/%m/%Y %H:%M', dayfirst=True
                    )
                    from datetime import timedelta
                    data_fim_dt = data_inicio_dt + timedelta(hours=estudo_info['duracao_horas'])
                except Exception as e:
                    raise RuntimeError(f"Período inválido no estudo '{estudo_info['nome']}': {e}")

                arquivos_com_dados = []
                for arq_idx, df_arq in gerador.dados_por_arquivo.items():
                    df_filtrado = df_arq[(df_arq['timestamp'] >= data_inicio_dt) &
                                        (df_arq['timestamp'] <= data_fim_dt)]
                    if len(df_filtrado) > 0:
                        arquivos_com_dados.append(arq_idx)

                if len(arquivos_com_dados) == 0:
                    raise RuntimeError(
                        f"Nenhum arquivo contém dados para o estudo '{estudo_info['nome']}' "
                        f"no período {data_inicio_dt.strftime('%d/%m/%Y %H:%M')} "
                        f"a {data_fim_dt.strftime('%d/%m/%Y %H:%M')}."
                    )
                if len(arquivos_com_dados) > 1:
                    raise RuntimeError(
                        f"Múltiplos arquivos contêm dados para o estudo '{estudo_info['nome']}'. "
                        f"Os arquivos devem ter períodos distintos."
                    )

                estudo_info['arquivo_idx'] = arquivos_com_dados[0]
                log_ui(f"  ✅ Estudo '{estudo_info['nome']}' → arquivo {arquivos_com_dados[0]}")

            # ── Criar estudos (temperatura + umidade) ──────────────────────
            gerador.estudos = []
            tem_umidade = any(gerador.sensores_por_arquivo[idx]['umidade']
                              for idx in gerador.sensores_por_arquivo)

            for estudo_info in gerador.estudos_info:
                arquivo_idx = estudo_info['arquivo_idx']
                estudo_temp = {k: v for k, v in estudo_info.items()}
                estudo_temp['tipo'] = 'temperatura'
                gerador.estudos.append(estudo_temp)

                if gerar_umidade and tem_umidade and gerador.sensores_por_arquivo[arquivo_idx]['umidade']:
                    estudo_umid = dict(estudo_temp)
                    estudo_umid['nome'] = estudo_info['nome'] + ' (Umidade)'
                    estudo_umid['tipo'] = 'umidade'
                    gerador.estudos.append(estudo_umid)

            # ── Sensores de doca ────────────────────────────────────────────
            if gerador.tipo_equipamento == "galpao":
                docas_por_arq = st.session_state.sensores_doca_por_estudo
                for idx_est, estudo in enumerate(gerador.estudos, 1):
                    arq_idx = estudo['arquivo_idx']
                    gerador.sensores_doca_por_estudo[idx_est] = docas_por_arq.get(arq_idx, [])
            else:
                for idx_est in range(1, len(gerador.estudos) + 1):
                    gerador.sensores_doca_por_estudo[idx_est] = []

            progress_bar.progress(60, text="Gerando PDF…")
            log_ui(f"Gerando PDF com {len(gerador.estudos)} estudo(s)…")

            # ── Gerar PDF ───────────────────────────────────────────────────
            from pathlib import Path as _Path

            # Criar pasta temporária simulando ~/Downloads
            tmpout = tempfile.mkdtemp()
            fake_downloads = os.path.join(tmpout, "Downloads")
            os.makedirs(fake_downloads, exist_ok=True)

            nome_pdf = f"relatorio_{gerador.tag}_{len(gerador.estudos)}_estudos_gxp.pdf"

            original_gerar_pdf = gerador.gerar_pdf

            def _gerar_pdf_patched():
                old_home = _Path.home
                _Path.home = staticmethod(lambda: _Path(tmpout))
                old_out = sys.stdout
                sys.stdout = io.StringIO()
                try:
                    resultado = original_gerar_pdf()
                finally:
                    _Path.home = old_home
                    sys.stdout = old_out
                return resultado

            arquivo_gerado = _gerar_pdf_patched()

            # Verificar pelo caminho retornado primeiro
            if not arquivo_gerado or not os.path.exists(arquivo_gerado):
                # Buscar recursivamente no tmpout
                pdfs_encontrados = list(_Path(tmpout).rglob("*.pdf"))
                if pdfs_encontrados:
                    arquivo_gerado = str(pdfs_encontrados[0])
                else:
                    # Último recurso: script salvou no ~/Downloads real
                    downloads_real = str(_Path.home() / "Downloads")
                    pdfs_real = sorted(
                        _Path(downloads_real).glob(f"*{gerador.tag}*.pdf"),
                        key=lambda p: p.stat().st_mtime, reverse=True
                    )
                    if pdfs_real:
                        arquivo_gerado = str(pdfs_real[0])
                    else:
                        raise RuntimeError(
                            "PDF não encontrado. Verifique se o script gera o PDF normalmente."
                        )

            progress_bar.progress(90, text="Lendo PDF gerado…")
            with open(arquivo_gerado, "rb") as f:
                pdf_bytes = f.read()

            st.session_state.pdf_bytes = pdf_bytes
            st.session_state.pdf_nome = nome_pdf
            st.session_state.log_path = gerador.log_path

            try:
                with open(gerador.log_path, "rb") as f:
                    st.session_state.log_bytes = f.read()
            except Exception:
                st.session_state.log_bytes = None

            progress_bar.progress(100, text="✅ Concluído!")
            log_ui("✅ Relatório gerado com sucesso!")

            st.success("✅ Relatório gerado com sucesso!")
            st.download_button(
                label="⬇️ Baixar Relatório PDF",
                data=pdf_bytes,
                file_name=nome_pdf,
                mime="application/pdf",
                type="primary",
            )
            if st.session_state.log_bytes:
                st.download_button(
                    label="⬇️ Baixar Log de Auditoria",
                    data=st.session_state.log_bytes,
                    file_name=os.path.basename(gerador.log_path),
                    mime="text/plain",
                )

        except Exception as e:
            progress_bar.progress(0, text="Erro")
            st.error(f"❌ Erro ao gerar relatório: {e}")
            with st.expander("Detalhes do erro"):
                st.code(traceback.format_exc())
