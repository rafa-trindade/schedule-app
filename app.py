# -*- coding: utf-8 -*-
"""
schedule-app - agenda pessoal em Flask, com CSV como "banco de dados".
"""
import csv
import os
import uuid
from datetime import date, datetime, timedelta

from flask import Flask, render_template, request, redirect, url_for, flash
import webview

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EVENTOS_CSV = os.path.join(BASE_DIR, "data", "eventos.csv")
CATEGORIAS_CSV = os.path.join(BASE_DIR, "data", "categorias.csv")
CONCLUSOES_CSV = os.path.join(BASE_DIR, "data", "conclusoes.csv")

EVENTOS_FIELDS = [
    "id",
    "categoria",
    "titulo",
    "descricao",
    "local",
    "data_inicio",
    "hora_inicio",
    "data_fim",
    "hora_fim",
    "dia_inteiro",
    "recorrencia",         # RRULE bruta, preservada de importações antigas do Google Calendar
    "recorrencia_tipo",    # "", "dias" ou "semana"
    "recorrencia_dias",    # usado quando recorrencia_tipo == "dias": repete a cada X dias
    "recorrencia_semana",  # usado quando recorrencia_tipo == "semana": ex "seg,qua,sex"
    "recorrencia_ate",     # opcional: data limite da recorrência
]

CATEGORIAS_FIELDS = ["id", "nome", "cor"]

# uma linha por (evento marcado como concluído, data específica da ocorrência) -
# separado dos eventos porque um evento recorrente pode estar concluído num dia
# e não estar em outro
CONCLUSOES_FIELDS = ["evento_id", "data"]

CATEGORIAS_PADRAO = [
    "Faculdade",
    "Jornada de Dados",
    "Leitura",
    "Outros",
    "PoD Academy",
    "Reservatório de Dopamina",
]

PALETA_CORES = [
    "#39d353", "#58a6ff", "#f778ba", "#d29922", "#a371f7", "#ff7b72",
    "#56d4dd", "#f0883e", "#7ee787", "#79c0ff", "#e3b341", "#ffa198",
]

DIAS_SEMANA = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
               "sexta-feira", "sábado", "domingo"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
          "agosto", "setembro", "outubro", "novembro", "dezembro"]

# índice bate com date.weekday() (0 = segunda ... 6 = domingo)
DIAS_SEMANA_ABREV = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]
DIAS_SEMANA_LABEL = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]

app = Flask(__name__)
app.secret_key = "agenda-local-dev-key"  # ok para uso local


# ---------------------------------------------------------------------------
# Camada de dados (CSV) - eventos
# ---------------------------------------------------------------------------
def garantir_eventos_csv():
    os.makedirs(os.path.dirname(EVENTOS_CSV), exist_ok=True)
    if not os.path.exists(EVENTOS_CSV):
        with open(EVENTOS_CSV, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=EVENTOS_FIELDS).writeheader()


def ler_eventos():
    garantir_eventos_csv()
    with open(EVENTOS_CSV, newline="", encoding="utf-8") as f:
        eventos = list(csv.DictReader(f))
    # normaliza eventos antigos que não tinham as colunas novas de recorrência
    for e in eventos:
        for campo in EVENTOS_FIELDS:
            e.setdefault(campo, "")
    return eventos


def salvar_eventos(eventos):
    with open(EVENTOS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=EVENTOS_FIELDS)
        writer.writeheader()
        for e in eventos:
            writer.writerow({campo: e.get(campo, "") for campo in EVENTOS_FIELDS})


# ---------------------------------------------------------------------------
# Camada de dados (CSV) - categorias
# ---------------------------------------------------------------------------
def garantir_categorias_csv():
    os.makedirs(os.path.dirname(CATEGORIAS_CSV), exist_ok=True)
    if not os.path.exists(CATEGORIAS_CSV):
        with open(CATEGORIAS_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CATEGORIAS_FIELDS)
            writer.writeheader()
            for i, nome in enumerate(CATEGORIAS_PADRAO):
                writer.writerow({
                    "id": str(uuid.uuid4())[:8],
                    "nome": nome,
                    "cor": PALETA_CORES[i % len(PALETA_CORES)],
                })


def ler_categorias():
    garantir_categorias_csv()
    with open(CATEGORIAS_CSV, newline="", encoding="utf-8") as f:
        categorias = list(csv.DictReader(f))
    categorias.sort(key=lambda c: c["nome"].lower())
    return categorias


def salvar_categorias(categorias):
    with open(CATEGORIAS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CATEGORIAS_FIELDS)
        writer.writeheader()
        writer.writerows(categorias)


def proxima_cor(categorias_existentes):
    usadas = {c["cor"] for c in categorias_existentes}
    for cor in PALETA_CORES:
        if cor not in usadas:
            return cor
    return PALETA_CORES[len(categorias_existentes) % len(PALETA_CORES)]


def proximo_id():
    return str(uuid.uuid4())[:8]


# ---------------------------------------------------------------------------
# Camada de dados (CSV) - conclusões (evento + data marcada como feita)
# ---------------------------------------------------------------------------
def garantir_conclusoes_csv():
    os.makedirs(os.path.dirname(CONCLUSOES_CSV), exist_ok=True)
    if not os.path.exists(CONCLUSOES_CSV):
        with open(CONCLUSOES_CSV, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CONCLUSOES_FIELDS).writeheader()


def ler_conclusoes():
    """Retorna um set de tuplas (evento_id, data_iso) marcadas como concluídas."""
    garantir_conclusoes_csv()
    with open(CONCLUSOES_CSV, newline="", encoding="utf-8") as f:
        return {(row["evento_id"], row["data"]) for row in csv.DictReader(f)}


def salvar_conclusoes(conjunto):
    with open(CONCLUSOES_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CONCLUSOES_FIELDS)
        writer.writeheader()
        for evento_id, data_iso in sorted(conjunto):
            writer.writerow({"evento_id": evento_id, "data": data_iso})


# ---------------------------------------------------------------------------
# Datas / recorrência
# ---------------------------------------------------------------------------
def parse_data(s):
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def data_sort_key(ev):
    d = parse_data(ev.get("data_inicio", ""))
    hora = ev.get("hora_inicio") or "00:00"
    if d is None:
        return datetime.min
    try:
        h, m = hora.split(":")
        return datetime(d.year, d.month, d.day, int(h), int(m))
    except ValueError:
        return datetime(d.year, d.month, d.day)


def data_extenso(d):
    return f"{DIAS_SEMANA[d.weekday()]}, {d.day} de {MESES[d.month - 1]} de {d.year}"


def evento_ocorre_em(evento, data_alvo):
    """Verifica se um evento (simples, 'a cada X dias' ou 'dias da semana') acontece em data_alvo."""
    di = parse_data(evento.get("data_inicio", ""))
    if di is None:
        return False
    df = parse_data(evento.get("data_fim", "")) or di
    if df < di:
        df = di
    duracao = (df - di).days

    tipo = (evento.get("recorrencia_tipo") or "").strip()
    dias_raw = (evento.get("recorrencia_dias") or "").strip()
    semana_raw = (evento.get("recorrencia_semana") or "").strip()

    # retrocompatibilidade: eventos criados antes de existir recorrencia_tipo
    # só tinham recorrencia_dias preenchido
    if not tipo and dias_raw:
        tipo = "dias"

    rec_ate = parse_data((evento.get("recorrencia_ate") or "").strip())

    if tipo == "semana" and semana_raw:
        if data_alvo < di:
            return False
        if rec_ate and data_alvo > rec_ate:
            return False
        codigos_selecionados = set(semana_raw.split(","))
        codigo_do_dia = DIAS_SEMANA_ABREV[data_alvo.weekday()]
        return codigo_do_dia in codigos_selecionados

    if tipo == "dias" and dias_raw:
        try:
            intervalo = int(dias_raw)
        except ValueError:
            return di <= data_alvo <= df
        if intervalo <= 0:
            return di <= data_alvo <= df
        if data_alvo < di:
            return False
        if rec_ate and data_alvo > rec_ate:
            return False
        delta = (data_alvo - di).days
        for offset in range(0, duracao + 1):
            rem = delta - offset
            if rem >= 0 and rem % intervalo == 0:
                return True
        return False

    return di <= data_alvo <= df


def descricao_recorrencia(evento):
    """Texto curto pra mostrar na listagem, ex: 'toda Seg/Qua/Sex' ou 'a cada 3d'."""
    tipo = (evento.get("recorrencia_tipo") or "").strip()
    dias_raw = (evento.get("recorrencia_dias") or "").strip()
    semana_raw = (evento.get("recorrencia_semana") or "").strip()

    if not tipo and dias_raw:
        tipo = "dias"

    if tipo == "semana" and semana_raw:
        codigos = [c for c in semana_raw.split(",") if c in DIAS_SEMANA_ABREV]
        labels = [DIAS_SEMANA_LABEL[DIAS_SEMANA_ABREV.index(c)] for c in codigos]
        return "/".join(labels) if labels else ""

    if tipo == "dias" and dias_raw:
        if dias_raw.strip() == "1":
            return "todos os dias"
        return f"a cada {dias_raw}d"

    return ""


def evento_futuro_ou_atual(evento, hoje=None):
    """True se o evento ainda tem (ou pode ter) alguma ocorrência hoje ou no futuro."""
    hoje = hoje or date.today()

    tipo = (evento.get("recorrencia_tipo") or "").strip()
    dias_raw = (evento.get("recorrencia_dias") or "").strip()
    semana_raw = (evento.get("recorrencia_semana") or "").strip()
    if not tipo and dias_raw:
        tipo = "dias"

    if tipo == "dias" or (tipo == "semana" and semana_raw):
        rec_ate = parse_data((evento.get("recorrencia_ate") or "").strip())
        return rec_ate is None or rec_ate >= hoje

    df = parse_data(evento.get("data_fim", "")) or parse_data(evento.get("data_inicio", ""))
    return df is not None and df >= hoje


# filtro jinja: exibe data ISO (yyyy-mm-dd) como dd/mm/aaaa
@app.template_filter("br_data")
def br_data(iso_str):
    d = parse_data(iso_str or "")
    return d.strftime("%d/%m/%Y") if d else (iso_str or "")


# filtro jinja: rótulo curto da recorrência de um evento
@app.template_filter("rec_label")
def rec_label(evento):
    return descricao_recorrencia(evento)


def ler_campos_recorrencia(form):
    """Lê os campos de recorrência do formulário, zerando o que não é do tipo escolhido."""
    tipo = form.get("recorrencia_tipo", "").strip()
    ate = form.get("recorrencia_ate", "").strip()

    if tipo == "dias":
        dias = form.get("recorrencia_dias", "").strip()
        semana = ""
    elif tipo == "semana":
        dias = ""
        codigos_validos = set(DIAS_SEMANA_ABREV)
        selecionados = [c for c in form.getlist("recorrencia_semana") if c in codigos_validos]
        semana = ",".join(selecionados)
        if not semana:
            tipo = ""  # nenhum dia marcado = não repete
    else:
        tipo, dias, semana = "", "", ""

    return tipo, dias, semana, ate


# ---------------------------------------------------------------------------
# Rotas - eventos
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    categorias = ler_categorias()
    nomes_categorias = [c["nome"] for c in categorias]
    cores_por_categoria = {c["nome"]: c["cor"] for c in categorias}

    categoria_filtro = request.args.get("categoria", "").strip()
    busca = request.args.get("q", "").strip().lower()
    apenas_futuros = request.args.get("futuros") == "1"

    modo_pedido = request.args.get("modo", "").strip()
    if busca:
        modo = "lista"
    elif modo_pedido in ("dia", "semana", "lista"):
        modo = modo_pedido
    else:
        modo = "dia"

    todos = ler_eventos()
    conclusoes = ler_conclusoes()
    hoje = date.today()

    # contagem do sidebar: só eventos que ainda têm ocorrência hoje/futura
    contagem = {
        nome: sum(1 for e in todos if e["categoria"] == nome and evento_futuro_ou_atual(e, hoje))
        for nome in nomes_categorias
    }
    total_futuros = sum(1 for e in todos if evento_futuro_ou_atual(e, hoje))

    if modo == "lista":
        eventos = todos
        if apenas_futuros:
            eventos = [e for e in eventos if evento_futuro_ou_atual(e, hoje)]
        if categoria_filtro:
            eventos = [e for e in eventos if e["categoria"] == categoria_filtro]
        if busca:
            eventos = [
                e for e in eventos
                if busca in e["titulo"].lower() or busca in e["descricao"].lower()
            ]
        eventos.sort(key=data_sort_key)
        return render_template(
            "index.html", modo="lista", eventos=eventos, categorias=categorias,
            categoria_filtro=categoria_filtro, busca=busca, contagem=contagem,
            cores_por_categoria=cores_por_categoria, total=len(todos),
            total_futuros=total_futuros, apenas_futuros=apenas_futuros,
            data_alvo_iso=hoje.isoformat(),
        )

    if modo == "semana":
        data_alvo = parse_data(request.args.get("data", "")) or hoje
        inicio_semana = data_alvo - timedelta(days=data_alvo.weekday())  # segunda-feira
        fim_semana = inicio_semana + timedelta(days=6)

        dias_semana = []
        for i in range(7):
            d = inicio_semana + timedelta(days=i)
            eventos_do_dia = [e for e in todos if evento_ocorre_em(e, d)]
            if categoria_filtro:
                eventos_do_dia = [e for e in eventos_do_dia if e["categoria"] == categoria_filtro]
            eventos_do_dia.sort(key=lambda e: (e.get("dia_inteiro") != "sim", e.get("hora_inicio") or "00:00"))
            for e in eventos_do_dia:
                e["_concluido"] = (e["id"], d.isoformat()) in conclusoes
            dias_semana.append({
                "data_iso": d.isoformat(),
                "dia_num": d.day,
                "mes_num": d.month,
                "label": DIAS_SEMANA_LABEL[d.weekday()],
                "eh_hoje": d == hoje,
                "eventos": eventos_do_dia,
            })

        if inicio_semana.month == fim_semana.month:
            semana_titulo = f"{inicio_semana.day} – {fim_semana.day} de {MESES[fim_semana.month - 1]} de {fim_semana.year}"
        else:
            semana_titulo = (
                f"{inicio_semana.day} de {MESES[inicio_semana.month - 1][:3]} – "
                f"{fim_semana.day} de {MESES[fim_semana.month - 1][:3]} de {fim_semana.year}"
            )

        return render_template(
            "index.html", modo="semana", categorias=categorias,
            categoria_filtro=categoria_filtro, busca=busca, contagem=contagem,
            cores_por_categoria=cores_por_categoria, total=len(todos),
            total_futuros=total_futuros,
            dias_semana=dias_semana, semana_titulo=semana_titulo,
            semana_atual=(inicio_semana <= hoje <= fim_semana),
            semana_anterior=(inicio_semana - timedelta(days=7)).isoformat(),
            semana_seguinte=(inicio_semana + timedelta(days=7)).isoformat(),
            data_alvo_iso=data_alvo.isoformat(),
        )

    # modo "dia": agenda do dia atual, navegável
    data_alvo = parse_data(request.args.get("data", "")) or hoje
    eventos_dia = [e for e in todos if evento_ocorre_em(e, data_alvo)]
    if categoria_filtro:
        eventos_dia = [e for e in eventos_dia if e["categoria"] == categoria_filtro]
    eventos_dia.sort(key=lambda e: (e.get("dia_inteiro") != "sim", e.get("hora_inicio") or "00:00"))
    for e in eventos_dia:
        e["_concluido"] = (e["id"], data_alvo.isoformat()) in conclusoes

    return render_template(
        "index.html", modo="dia", eventos=eventos_dia, categorias=categorias,
        categoria_filtro=categoria_filtro, busca=busca, contagem=contagem,
        cores_por_categoria=cores_por_categoria, total=len(todos),
        total_futuros=total_futuros,
        data_alvo_iso=data_alvo.isoformat(),
        data_alvo_extenso=data_extenso(data_alvo),
        dia_anterior=(data_alvo - timedelta(days=1)).isoformat(),
        dia_seguinte=(data_alvo + timedelta(days=1)).isoformat(),
        eh_hoje=(data_alvo == hoje),
    )


@app.route("/evento/<evento_id>/concluir", methods=["POST"])
def alternar_concluido(evento_id):
    data_iso = request.form.get("data", "").strip()
    voltar_para = request.form.get("voltar_para") or url_for("index")

    if data_iso:
        conclusoes = ler_conclusoes()
        chave = (evento_id, data_iso)
        if chave in conclusoes:
            conclusoes.discard(chave)
        else:
            conclusoes.add(chave)
        salvar_conclusoes(conclusoes)

    return redirect(voltar_para)


@app.route("/evento/novo", methods=["GET", "POST"])
def novo_evento():
    categorias = ler_categorias()
    categoria_padrao = categorias[0]["nome"] if categorias else ""

    if request.method == "POST":
        eventos = ler_eventos()
        tipo, dias, semana, ate = ler_campos_recorrencia(request.form)
        novo = {
            "id": proximo_id(),
            "categoria": request.form.get("categoria", categoria_padrao),
            "titulo": request.form.get("titulo", "").strip(),
            "descricao": request.form.get("descricao", "").strip(),
            "local": request.form.get("local", "").strip(),
            "data_inicio": request.form.get("data_inicio", ""),
            "hora_inicio": request.form.get("hora_inicio", ""),
            "data_fim": request.form.get("data_fim") or request.form.get("data_inicio", ""),
            "hora_fim": request.form.get("hora_fim", ""),
            "dia_inteiro": "sim" if request.form.get("dia_inteiro") == "on" else "nao",
            "recorrencia": "",
            "recorrencia_tipo": tipo,
            "recorrencia_dias": dias,
            "recorrencia_semana": semana,
            "recorrencia_ate": ate,
        }
        if not novo["titulo"] or not novo["data_inicio"]:
            flash("Título e data de início são obrigatórios.", "erro")
            return render_template("form.html", categorias=categorias, evento=novo, modo="novo")

        eventos.append(novo)
        salvar_eventos(eventos)
        flash("Evento criado com sucesso.", "ok")
        return redirect(url_for("index"))

    evento_vazio = {c: "" for c in EVENTOS_FIELDS}
    evento_vazio["categoria"] = categoria_padrao
    evento_vazio["data_inicio"] = request.args.get("data", "")
    return render_template("form.html", categorias=categorias, evento=evento_vazio, modo="novo")


@app.route("/evento/<evento_id>/editar", methods=["GET", "POST"])
def editar_evento(evento_id):
    eventos = ler_eventos()
    categorias = ler_categorias()
    evento = next((e for e in eventos if e["id"] == evento_id), None)
    if evento is None:
        flash("Evento não encontrado.", "erro")
        return redirect(url_for("index"))

    if request.method == "POST":
        evento["categoria"] = request.form.get("categoria", evento["categoria"])
        evento["titulo"] = request.form.get("titulo", "").strip()
        evento["descricao"] = request.form.get("descricao", "").strip()
        evento["local"] = request.form.get("local", "").strip()
        evento["data_inicio"] = request.form.get("data_inicio", "")
        evento["hora_inicio"] = request.form.get("hora_inicio", "")
        evento["data_fim"] = request.form.get("data_fim") or evento["data_inicio"]
        evento["hora_fim"] = request.form.get("hora_fim", "")
        evento["dia_inteiro"] = "sim" if request.form.get("dia_inteiro") == "on" else "nao"
        tipo, dias, semana, ate = ler_campos_recorrencia(request.form)
        evento["recorrencia_tipo"] = tipo
        evento["recorrencia_dias"] = dias
        evento["recorrencia_semana"] = semana
        evento["recorrencia_ate"] = ate

        if not evento["titulo"] or not evento["data_inicio"]:
            flash("Título e data de início são obrigatórios.", "erro")
            return render_template("form.html", categorias=categorias, evento=evento, modo="editar")

        salvar_eventos(eventos)
        flash("Evento atualizado com sucesso.", "ok")
        return redirect(url_for("index"))

    return render_template("form.html", categorias=categorias, evento=evento, modo="editar")


@app.route("/evento/<evento_id>/excluir", methods=["POST"])
def excluir_evento(evento_id):
    eventos = ler_eventos()
    eventos = [e for e in eventos if e["id"] != evento_id]
    salvar_eventos(eventos)
    flash("Evento excluído.", "ok")
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Rotas - categorias
# ---------------------------------------------------------------------------
@app.route("/categorias")
def listar_categorias():
    categorias = ler_categorias()
    eventos = ler_eventos()
    contagem = {c["nome"]: sum(1 for e in eventos if e["categoria"] == c["nome"]) for c in categorias}
    return render_template("categorias.html", categorias=categorias, contagem=contagem)


@app.route("/categorias/nova", methods=["POST"])
def nova_categoria():
    categorias = ler_categorias()
    nome = request.form.get("nome", "").strip()

    if not nome:
        flash("Informe um nome para a categoria.", "erro")
        return redirect(url_for("listar_categorias"))

    if any(c["nome"].lower() == nome.lower() for c in categorias):
        flash(f'Já existe uma categoria chamada "{nome}".', "erro")
        return redirect(url_for("listar_categorias"))

    cor = request.form.get("cor", "").strip() or proxima_cor(categorias)
    categorias.append({"id": proximo_id(), "nome": nome, "cor": cor})
    salvar_categorias(categorias)
    flash(f'Categoria "{nome}" criada.', "ok")
    return redirect(url_for("listar_categorias"))


@app.route("/categorias/<categoria_id>/editar", methods=["GET", "POST"])
def editar_categoria(categoria_id):
    categorias = ler_categorias()
    categoria = next((c for c in categorias if c["id"] == categoria_id), None)
    if categoria is None:
        flash("Categoria não encontrada.", "erro")
        return redirect(url_for("listar_categorias"))

    if request.method == "POST":
        novo_nome = request.form.get("nome", "").strip()
        nova_cor = request.form.get("cor", "").strip() or categoria["cor"]

        if not novo_nome:
            flash("Informe um nome para a categoria.", "erro")
            return render_template("categoria_form.html", categoria=categoria)

        if any(c["nome"].lower() == novo_nome.lower() and c["id"] != categoria_id for c in categorias):
            flash(f'Já existe uma categoria chamada "{novo_nome}".', "erro")
            return render_template("categoria_form.html", categoria=categoria)

        nome_antigo = categoria["nome"]
        categoria["nome"] = novo_nome
        categoria["cor"] = nova_cor
        salvar_categorias(categorias)

        if nome_antigo != novo_nome:
            eventos = ler_eventos()
            alterados = False
            for e in eventos:
                if e["categoria"] == nome_antigo:
                    e["categoria"] = novo_nome
                    alterados = True
            if alterados:
                salvar_eventos(eventos)

        flash("Categoria atualizada.", "ok")
        return redirect(url_for("listar_categorias"))

    return render_template("categoria_form.html", categoria=categoria)


@app.route("/categorias/<categoria_id>/excluir", methods=["POST"])
def excluir_categoria(categoria_id):
    categorias = ler_categorias()
    categoria = next((c for c in categorias if c["id"] == categoria_id), None)
    if categoria is None:
        flash("Categoria não encontrada.", "erro")
        return redirect(url_for("listar_categorias"))

    eventos = ler_eventos()
    em_uso = sum(1 for e in eventos if e["categoria"] == categoria["nome"])

    acao = request.form.get("acao", "bloquear")
    destino = request.form.get("mover_para", "").strip()

    if em_uso and acao == "bloquear":
        flash(
            f'A categoria "{categoria["nome"]}" tem {em_uso} evento(s). '
            f'Use a opção "mover e excluir" para reatribuir os eventos antes de excluir.',
            "erro",
        )
        return redirect(url_for("listar_categorias"))

    if em_uso and acao == "mover":
        if not destino or destino == categoria["nome"]:
            flash("Escolha uma categoria de destino válida para mover os eventos.", "erro")
            return redirect(url_for("listar_categorias"))
        for e in eventos:
            if e["categoria"] == categoria["nome"]:
                e["categoria"] = destino
        salvar_eventos(eventos)

    categorias = [c for c in categorias if c["id"] != categoria_id]
    salvar_categorias(categorias)
    flash(f'Categoria "{categoria["nome"]}" excluída.', "ok")
    return redirect(url_for("listar_categorias"))


if __name__ == "__main__":
    garantir_eventos_csv()
    garantir_categorias_csv()
    garantir_conclusoes_csv()
    # Cria a janela apontando diretamente para o app Flask
    webview.create_window('Minha Agenda', app, width=1280, height=800)
    
    # Inicia a interface (isso já roda o servidor Flask junto)
    webview.start()
