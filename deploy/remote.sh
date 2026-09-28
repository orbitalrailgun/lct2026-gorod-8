#!/usr/bin/env bash
#
# Развёртывание демонстрационного стенда на удалённой машине по SSH.
#
# Сценарий простой: синхронизировать исходники, собрать образ на той стороне,
# поднять, дождаться готовности и сказать адрес. Скрипт идемпотентный —
# повторный запуск обновляет стенд, не трогая накопленные данные.
#
# Что НЕ копируется на удалённую машину: датасет, витрины, обученные модели,
# каталог .git и документация. Приложению они не нужны — вердикты уезжают
# в образ компактным файлом инициализации. Благодаря этому развёртывание
# занимает минуты, а не часы.
#
# По умолчанию стенд слушает только 127.0.0.1 и в сеть не выставлен.
# Доступ — туннелем SSH (команда tunnel) или через обратный прокси с TLS
# на той же машине. Открыть порт наружу можно, но только осознанно:
# CONCORDE_BIND=0.0.0.0.
#
# Настройка через переменные окружения:
#
#   CONCORDE_HOST       обязательно: user@host удалённой машины
#   CONCORDE_SSH_PORT   порт SSH, по умолчанию 22
#   CONCORDE_DIR        каталог на удалённой машине, по умолчанию ~/concorde
#   CONCORDE_APP_PORT   порт приложения на стенде, по умолчанию 8080
#   CONCORDE_BIND       адрес привязки, по умолчанию 127.0.0.1 (только петля)
#   CONCORDE_LOCAL_PORT локальный порт туннеля
#   CONCORDE_URL_HOST   имя для итоговой ссылки, если оно отличается от адреса SSH
#   CONCORDE_SSH_OPTS   дополнительные ключи ssh, например -i ~/.ssh/stand
#
# Примеры:
#
#   CONCORDE_HOST=root@203.0.113.10 deploy/remote.sh deploy
#   CONCORDE_HOST=user@stand.example deploy/remote.sh status
#   CONCORDE_HOST=user@stand.example deploy/remote.sh reset
#
set -euo pipefail

HOST="${CONCORDE_HOST:-}"
SSH_PORT="${CONCORDE_SSH_PORT:-22}"
REMOTE_DIR="${CONCORDE_DIR:-concorde}"
APP_PORT="${CONCORDE_APP_PORT:-8080}"
SSH_OPTS="${CONCORDE_SSH_OPTS:-}"
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Адрес, на котором стенд слушает НА УДАЛЁННОЙ МАШИНЕ. По умолчанию
# петлевой: сервер с публичным адресом иначе открывает стенд всему
# интернету вместе со страницей входа, показывающей одноразовый код.
# Доступ — через туннель SSH (команда tunnel) или обратный прокси.
BIND_ADDR="${CONCORDE_BIND:-127.0.0.1}"

# Порт на вашей машине, куда пробрасывается туннель.
LOCAL_PORT="${CONCORDE_LOCAL_PORT:-$APP_PORT}"

URL_HOST="${CONCORDE_URL_HOST:-${HOST#*@}}"
PUBLIC_URL="http://${URL_HOST}:${APP_PORT}"
TUNNEL_URL="http://localhost:${LOCAL_PORT}"

# Слушает ли стенд только петлю.
is_loopback() { [ "$BIND_ADDR" = "127.0.0.1" ] || [ "$BIND_ADDR" = "localhost" ]; }

# Одно соединение на весь запуск.
#
# Скрипт обращается к машине полтора десятка раз: проверка, синхронизация,
# определение compose, сборка, запуск, опрос готовности. Каждое обращение —
# отдельное подключение, и при входе по паролю ssh спрашивал бы пароль
# заново каждый раз. Мультиплексирование открывает одно соединение,
# а остальные идут через него: пароль вводится единожды.
#
# Путь к управляющему сокету короткий намеренно: у сокетов Unix предел
# длины пути около сотни символов, а временные каталоги в macOS длинные.
CONTROL_PATH="/tmp/.concorde-ssh-$$"
SHARED_OPTS="-o ControlMaster=auto -o ControlPath=$CONTROL_PATH -o ControlPersist=10m"
MASTER_OPEN=no

close_master() {
    if [ "$MASTER_OPEN" = yes ]; then
        # shellcheck disable=SC2086
        ssh -O exit -o ControlPath="$CONTROL_PATH" -p "$SSH_PORT" $SSH_OPTS "$HOST" \
            >/dev/null 2>&1 || true
        MASTER_OPEN=no
    fi
}
trap close_master EXIT INT TERM

open_master() {
    [ "$MASTER_OPEN" = no ] || return 0
    [ -S "$CONTROL_PATH" ] && { MASTER_OPEN=yes; return 0; }

    say "Подключение к $HOST"
    info "если вход по паролю — он будет запрошен один раз на весь запуск"
    # shellcheck disable=SC2086
    if ssh -M -N -f -o ControlPath="$CONTROL_PATH" -o ControlPersist=10m \
           -p "$SSH_PORT" $SSH_OPTS "$HOST"; then
        MASTER_OPEN=yes
        info "соединение установлено"
    else
        die "не удалось подключиться по SSH к $HOST (порт $SSH_PORT).
  Проверьте адрес, пароль или ключ и доступность машины.
  Вход по ключу настраивается командой: deploy/remote.sh keys"
    fi
}

# ------------------------------------------------------------------ вывод

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '  \033[33m%s\033[0m\n' "$*"; }
die()  { printf '\n\033[31mОшибка: %s\033[0m\n\n' "$*" >&2; exit 1; }

require_host() {
    [ -n "$HOST" ] || die "не задан CONCORDE_HOST.
  Пример: CONCORDE_HOST=user@203.0.113.10 deploy/remote.sh deploy"
}

# Одна точка входа для всех удалённых команд: и ключи ssh, и каталог,
# и режим остановки при ошибке задаются здесь, а не в каждом вызове.
remote() {
    # shellcheck disable=SC2086
    ssh $SHARED_OPTS -p "$SSH_PORT" $SSH_OPTS "$HOST" \
        "set -e; cd '$REMOTE_DIR' 2>/dev/null || true; $*"
}

remote_raw() {
    # shellcheck disable=SC2086
    ssh $SHARED_OPTS -p "$SSH_PORT" $SSH_OPTS "$HOST" "$*"
}

# Docker Compose бывает как плагином (docker compose), так и отдельной
# программой (docker-compose). Определяем один раз и дальше не думаем.
COMPOSE=""

compose_cmd() {
    if [ -z "$COMPOSE" ]; then
        COMPOSE="$(remote_raw "if docker compose version >/dev/null 2>&1; then echo 'docker compose';
                    elif command -v docker-compose >/dev/null 2>&1; then echo 'docker-compose';
                    else echo ''; fi")"
    fi
    printf '%s' "$COMPOSE"
}

# Адрес привязки и порт передаются команде, а не хранятся в .env:
# так их можно менять между развёртываниями, не рискуя рассинхронизировать
# файл с секретами, который на стенде не перезаписывается.
compose_run() {
    remote "BIND_ADDR='$BIND_ADDR' APP_PORT='$APP_PORT' $(compose_cmd) $*"
}

# ----------------------------------------------------------------- проверки

connect() {
    require_host
    open_master
}

cmd_check() {
    connect
    say "Проверка удалённой машины"

    local system
    system="$(remote_raw "uname -sm; . /etc/os-release 2>/dev/null && echo \$PRETTY_NAME" | tr '\n' ' ')"
    info "Система: $system"

    remote_raw "command -v docker >/dev/null" \
        || die "на удалённой машине нет docker.
  Установить можно командой: deploy/remote.sh install-docker
  Либо вручную: curl -fsSL https://get.docker.com | sh"
    info "Docker: $(remote_raw 'docker --version')"

    local compose
    compose="$(compose_cmd)"
    [ -n "$compose" ] || die "на удалённой машине нет Docker Compose.
  Обычно он идёт плагином к docker: пакет docker-compose-plugin."
    info "Compose: $compose"

    remote_raw "docker info >/dev/null 2>&1" \
        || die "docker установлен, но недоступен текущему пользователю.
  Либо запускайте от root, либо добавьте пользователя в группу docker:
  sudo usermod -aG docker \$USER && выйти/войти заново."
    info "Права на docker: есть"

    local space
    space="$(remote_raw "df -h . | awk 'NR==2 {print \$4}'")"
    info "Свободно на диске: $space"
}

cmd_install_docker() {
    connect
    say "Установка Docker на удалённой машине"
    warn "Команда ставит docker официальным скриптом get.docker.com."
    warn "Она требует прав root и меняет состояние машины."
    printf '  Продолжить? [y/N] '
    read -r answer
    [ "$answer" = "y" ] || [ "$answer" = "Y" ] || die "отменено"

    remote_raw "curl -fsSL https://get.docker.com -o /tmp/get-docker.sh && sh /tmp/get-docker.sh"
    remote_raw "systemctl enable --now docker" || warn "не удалось включить автозапуск docker"
    info "Готово. Проверьте: deploy/remote.sh check"
}

# --------------------------------------------------------------- синхронизация

sync_sources() {
    say "Синхронизация исходников в $HOST:$REMOTE_DIR"
    remote_raw "mkdir -p '$REMOTE_DIR'"

    # Исключения повторяют .dockerignore и .gitignore: данные, модели,
    # виртуальное окружение и история git на стенде не нужны.
    # shellcheck disable=SC2086
    rsync -az --delete \
        -e "ssh $SHARED_OPTS -p $SSH_PORT $SSH_OPTS" \
        --exclude '.git/' \
        --exclude '.venv/' \
        `# .env создаётся на стенде и живёт только там. Без этого` \
        `# исключения --delete стирал бы его при каждом развёртывании,` \
        `# пароль базы генерировался бы заново и переставал совпадать` \
        `# с тем, что зафиксирован в томе при первом запуске.` \
        --exclude '.env' \
        --exclude 'data/' \
        --exclude 'dataset/' \
        --exclude 'dataset.zip' \
        --exclude 'models/*.pkl' \
        --exclude 'docs/' \
        --exclude 'tests/' \
        --exclude 'reports/' \
        --exclude '__pycache__/' \
        --exclude '*.webm' \
        --exclude '*.xlsx' \
        --exclude 'ТЗ/' \
        --exclude '.nicegui/' \
        "$LOCAL_DIR/" "$HOST:$REMOTE_DIR/"

    local size
    size="$(remote_raw "du -sh '$REMOTE_DIR' | cut -f1")"
    info "Перенесено, размер на стенде: $size"
}

# Секреты создаются на самой удалённой машине и туда же остаются.
# Так они не проходят через локальную историю команд и не попадают в логи.
ensure_secrets() {
    say "Секреты стенда"
    local exists
    exists="$(remote_raw "test -f '$REMOTE_DIR/.env' && echo yes || echo no")"

    if [ "$exists" = "yes" ]; then
        info ".env уже есть — пароль базы и секрет сессий сохраняются"
        return
    fi

    remote_raw "cd '$REMOTE_DIR' && {
        echo '# Создано deploy/remote.sh при первом развёртывании.';
        echo '# Пароль базы менять нельзя: он зафиксирован при создании тома.';
        printf 'POSTGRES_PASSWORD=%s\n' \"\$(openssl rand -hex 24 2>/dev/null || head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')\";
        printf 'SESSION_SECRET=%s\n'   \"\$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')\";
    } > .env && chmod 600 .env"
    info "Создан .env со случайным паролем базы и секретом сессий"
}

# ------------------------------------------------------------------ развёртывание

cmd_deploy() {
    connect
    cmd_check
    sync_sources
    ensure_secrets

    say "Сборка образа на стенде"
    info "первая сборка занимает несколько минут: ставятся зависимости"
    compose_run build

    say "Запуск"
    if is_loopback; then
        info "стенд слушает только 127.0.0.1 — снаружи он недоступен"
    else
        warn "стенд слушает на $BIND_ADDR: порт будет открыт в сеть"
    fi
    compose_run up -d

    wait_for_health
    print_summary
}

# Готовность проверяется с двух сторон, и для признания стенда живым
# достаточно любой. Изнутри машины — обычный случай; снаружи — запасной,
# он выручает, если на сервере нет curl или порт опубликован не на петле.
#
# А вот выводы из двух проверок разные. При петлевой привязке недоступность
# снаружи — не ошибка, а сам смысл настройки; доступность же означает,
# что порт кто-то открыл помимо нас. При открытой привязке всё наоборот.
health_inside()  { remote "curl -fsS http://localhost:$APP_PORT/api/health >/dev/null 2>&1"; }
health_outside() { curl -fsS -m 10 "$PUBLIC_URL/api/health" >/dev/null 2>&1; }

wait_for_health() {
    say "Ожидание готовности"
    local attempt=0 inside=no outside=no
    while [ "$attempt" -lt 60 ]; do
        if health_inside;  then inside=yes;  fi
        if health_outside; then outside=yes; fi
        if [ "$inside" = yes ] || [ "$outside" = yes ]; then break; fi
        attempt=$((attempt + 1))
        sleep 2
    done

    if [ "$inside" = no ] && [ "$outside" = no ]; then
        warn "сервис не ответил за две минуты, последние строки журнала:"
        compose_run logs --tail 30 app || true
        die "стенд не поднялся"
    fi
    info "сервис отвечает"

    if is_loopback; then
        if [ "$outside" = yes ]; then
            warn "стенд отвечает снаружи, хотя должен слушать только петлю."
            warn "Проверьте, не публикует ли порт что-то ещё: прокси, iptables,"
            warn "или сам docker в нестандартной сетевой настройке."
        else
            info "снаружи закрыт — как и задумано"
        fi
        return
    fi

    if [ "$outside" = yes ]; then
        info "снаружи сервис доступен: $PUBLIC_URL"
    else
        warn "снаружи порт $APP_PORT недоступен."
        warn "Сервис работает, но закрыт сетью. Обычно нужно открыть порт:"
        warn "  облачная security group — разрешить входящий TCP $APP_PORT;"
        warn "  ufw — sudo ufw allow $APP_PORT/tcp;"
        warn "  firewalld — sudo firewall-cmd --add-port=$APP_PORT/tcp --permanent && sudo firewall-cmd --reload"
    fi
}

print_summary() {
    say "Стенд развёрнут"

    if is_loopback; then
        info "Стенд слушает 127.0.0.1:$APP_PORT на удалённой машине"
        info "и недоступен из сети. Открыть его можно двумя способами."
        info ""
        info "1. Туннель SSH — ничего настраивать не нужно:"
        printf '     \033[1m%s\033[0m\n' "$(tunnel_command)"
        info "   затем открыть $TUNNEL_URL"
        info "   Короткая форма: deploy/remote.sh tunnel"
        info ""
        info "2. Обратный прокси с TLS на той же машине — для публичного"
        info "   доступа на время экспертизы. Прокси ходит на 127.0.0.1:$APP_PORT."
    else
        printf '  Адрес:  \033[1m%s\033[0m\n' "$PUBLIC_URL"
        info "Проверка: $PUBLIC_URL/api/health"
        warn ""
        warn "Порт открыт в сеть. Страница входа показывает одноразовый код,"
        warn "то есть войти может любой, кто знает адрес. Для публичного стенда"
        warn "это допустимо только на время экспертизы."
    fi

    info ""
    info "Вход: выберите пользователя, раскройте «Код для демонстрации»,"
    info "нажмите «Показать» и введите шестизначный код."
    info "Роли: ods (всё), dispatcher (район), tehnik (просмотр), brigade."
}

tunnel_command() {
    local opts="$SSH_OPTS"
    printf 'ssh -N -L %s:127.0.0.1:%s -p %s %s %s' \
        "$LOCAL_PORT" "$APP_PORT" "$SSH_PORT" "$opts" "$HOST"
}

cmd_tunnel() {
    require_host
    say "Туннель к стенду"
    info "Локальный порт $LOCAL_PORT → 127.0.0.1:$APP_PORT на $HOST"
    info "Откройте $TUNNEL_URL"
    info "Завершить — Ctrl+C."
    info ""
    # shellcheck disable=SC2086
    # Туннель живёт долго и своим соединением: общее закроется по выходе.
    exec ssh -N -L "$LOCAL_PORT:127.0.0.1:$APP_PORT" -p "$SSH_PORT" $SSH_OPTS "$HOST"
}

# ------------------------------------------------------------ эксплуатация

cmd_status() {
    connect
    say "Состояние стенда"
    compose_run ps
    info ""
    local health
    health="$(remote "curl -fsS http://localhost:$APP_PORT/api/health" 2>/dev/null \
              || curl -fsS -m 10 "$PUBLIC_URL/api/health" 2>/dev/null \
              || echo 'не отвечает')"
    info "Здоровье: $health"
    info "Адрес: $PUBLIC_URL"
}

cmd_logs() {
    connect
    compose_run logs --tail "${1:-80}" app
}

cmd_restart() {
    connect
    say "Перезапуск"
    compose_run restart app
    wait_for_health
}

cmd_reset() {
    connect
    say "Возврат к исходному набору вердиктов"
    warn "Будут удалены вердикты, решения и черновики заявок."
    printf '  Продолжить? [y/N] '
    read -r answer
    [ "$answer" = "y" ] || [ "$answer" = "Y" ] || die "отменено"

    compose_run exec -T db psql -U concorde -c "'TRUNCATE verdict CASCADE;'"
    compose_run exec -T app python -m scripts.06_seed_db
    info "исходный набор загружен"
}

cmd_down() {
    connect
    say "Остановка стенда"
    compose_run down
    info "контейнеры остановлены, данные сохранены"
    info "для полного удаления данных на стенде: docker compose down -v"
}

cmd_keys() {
    require_host
    say "Настройка входа по ключу"
    info "Пароль спросят один раз — при копировании ключа."
    info "После этого скрипт перестанет спрашивать его вовсе."

    local key="${CONCORDE_KEY:-$HOME/.ssh/id_ed25519}"
    if [ ! -f "$key" ]; then
        info "Ключа $key нет, создаю."
        ssh-keygen -t ed25519 -N "" -f "$key" || die "не удалось создать ключ"
    fi

    # ssh-copy-id намеренно не используется: он принимает ключ своим -i,
    # и если тот же -i уже есть в CONCORDE_SSH_OPTS, программа спотыкается
    # о повтор и печатает справку вместо работы. Переносимый путь короче
    # и ведёт себя одинаково везде.
    #
    # sort -u на той стороне убирает повторы: команду можно запускать
    # сколько угодно раз, файл ключей не распухнет.
    # shellcheck disable=SC2086
    ssh -p "$SSH_PORT" $SSH_OPTS "$HOST" \
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh \
         && cat >> ~/.ssh/authorized_keys \
         && sort -u -o ~/.ssh/authorized_keys ~/.ssh/authorized_keys \
         && chmod 600 ~/.ssh/authorized_keys" < "$key.pub" \
        || die "не удалось скопировать ключ на $HOST"

    info "Готово. Проверка: deploy/remote.sh check"
    info "Если ключ лежит не по умолчанию, добавляйте его к вызовам:"
    info "  CONCORDE_SSH_OPTS=\"-i $key\""
}

cmd_url() {
    require_host
    if is_loopback; then
        printf '%s\n' "$TUNNEL_URL"
        printf 'через туннель: %s\n' "$(tunnel_command)" >&2
    else
        printf '%s\n' "$PUBLIC_URL"
    fi
}

usage() {
    cat <<'USAGE'
Развёртывание демонстрационного стенда по SSH.

  deploy/remote.sh <команда>

Команды:
  deploy          синхронизировать, собрать, поднять, проверить (по умолчанию)
  check           проверить доступность машины, docker и права
  install-docker  поставить docker официальным скриптом (спросит подтверждение)
  status          состояние контейнеров и проверка здоровья
  logs [N]        последние N строк журнала приложения
  restart         перезапустить приложение
  reset           вернуть исходный набор вердиктов (спросит подтверждение)
  down            остановить стенд, данные сохранить
  tunnel          открыть туннель SSH к стенду и держать его
  keys            настроить вход по ключу, чтобы не вводить пароль
  url             напечатать адрес стенда

Переменные окружения:
  CONCORDE_HOST       user@host удалённой машины (обязательно)
  CONCORDE_SSH_PORT   порт SSH, по умолчанию 22
  CONCORDE_DIR        каталог на стенде, по умолчанию concorde
  CONCORDE_APP_PORT   порт приложения на стенде, по умолчанию 8080
  CONCORDE_BIND       адрес привязки на стенде, по умолчанию 127.0.0.1;
                      0.0.0.0 открывает порт в сеть — задавайте осознанно
  CONCORDE_LOCAL_PORT локальный порт туннеля, по умолчанию равен порту стенда
  CONCORDE_URL_HOST   имя для ссылки, если отличается от адреса SSH
  CONCORDE_SSH_OPTS   дополнительные ключи ssh, например: -i ~/.ssh/stand

Пример:
  CONCORDE_HOST=root@203.0.113.10 deploy/remote.sh deploy
USAGE
}

case "${1:-deploy}" in
    deploy)          cmd_deploy ;;
    check)           cmd_check ;;
    install-docker)  cmd_install_docker ;;
    status)          cmd_status ;;
    logs)            cmd_logs "${2:-80}" ;;
    restart)         cmd_restart ;;
    reset)           cmd_reset ;;
    down)            cmd_down ;;
    tunnel)          cmd_tunnel ;;
    keys)            cmd_keys ;;
    url)             cmd_url ;;
    -h|--help|help)  usage ;;
    *)               usage; die "неизвестная команда: $1" ;;
esac
