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
# Настройка через переменные окружения:
#
#   CONCORDE_HOST       обязательно: user@host удалённой машины
#   CONCORDE_SSH_PORT   порт SSH, по умолчанию 22
#   CONCORDE_DIR        каталог на удалённой машине, по умолчанию ~/concorde
#   CONCORDE_APP_PORT   порт приложения, по умолчанию 8080
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

URL_HOST="${CONCORDE_URL_HOST:-${HOST#*@}}"
PUBLIC_URL="http://${URL_HOST}:${APP_PORT}"

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
    ssh -p "$SSH_PORT" $SSH_OPTS "$HOST" "set -e; cd '$REMOTE_DIR' 2>/dev/null || true; $*"
}

remote_raw() {
    # shellcheck disable=SC2086
    ssh -p "$SSH_PORT" $SSH_OPTS "$HOST" "$*"
}

# Docker Compose бывает как плагином (docker compose), так и отдельной
# программой (docker-compose). Определяем один раз и дальше не думаем.
compose_cmd() {
    remote_raw "if docker compose version >/dev/null 2>&1; then echo 'docker compose';
                elif command -v docker-compose >/dev/null 2>&1; then echo 'docker-compose';
                else echo ''; fi"
}

# ----------------------------------------------------------------- проверки

cmd_check() {
    require_host
    say "Проверка удалённой машины"

    remote_raw "echo ok" >/dev/null 2>&1 \
        || die "не удалось подключиться по SSH к $HOST (порт $SSH_PORT).
  Проверьте адрес, ключ и то, что машина доступна."
    info "SSH: подключение есть"

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
    require_host
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
        -e "ssh -p $SSH_PORT $SSH_OPTS" \
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
        printf 'APP_PORT=%s\n' '$APP_PORT';
    } > .env && chmod 600 .env"
    info "Создан .env со случайным паролем базы и секретом сессий"
}

# ------------------------------------------------------------------ развёртывание

cmd_deploy() {
    require_host
    cmd_check
    sync_sources
    ensure_secrets

    local compose
    compose="$(compose_cmd)"

    say "Сборка образа на стенде"
    info "первая сборка занимает несколько минут: ставятся зависимости"
    remote "$compose build"

    say "Запуск"
    remote "$compose up -d"

    wait_for_health
    print_summary
}

# Готовность проверяется с двух сторон, и достаточно любой.
# Изнутри машины — обычный случай. Снаружи — тот, когда порт опубликован
# не на петлевом интерфейсе: так бывает при нестандартной сети docker
# и при развёртывании в контейнер.
health_inside()  { remote "curl -fsS http://localhost:$APP_PORT/api/health >/dev/null 2>&1"; }
health_outside() { curl -fsS -m 10 "$PUBLIC_URL/api/health" >/dev/null 2>&1; }

wait_for_health() {
    say "Ожидание готовности"
    local attempt=0 inside=no outside=no
    while [ "$attempt" -lt 60 ]; do
        if health_inside; then inside=yes; fi
        if health_outside; then outside=yes; fi
        [ "$inside" = yes ] || [ "$outside" = yes ] && break
        attempt=$((attempt + 1))
        sleep 2
    done

    if [ "$inside" = no ] && [ "$outside" = no ]; then
        warn "сервис не ответил за две минуты, последние строки журнала:"
        remote "$(compose_cmd) logs --tail 30 app" || true
        die "стенд не поднялся"
    fi

    [ "$inside" = yes ] && info "изнутри машины сервис отвечает"

    # Проверяем внешнюю доступность ещё раз: она могла появиться позже.
    if [ "$outside" = yes ] || health_outside; then
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
    printf '  Адрес:  \033[1m%s\033[0m\n' "$PUBLIC_URL"
    info "Проверка: $PUBLIC_URL/api/health"
    info ""
    info "Вход: выберите пользователя, раскройте «Код для демонстрации»,"
    info "нажмите «Показать» и введите шестизначный код."
    info "Роли: ods (всё), dispatcher (район), tehnik (просмотр), brigade."
    info ""
    warn "Стенд работает по HTTP и открыт любому, кто знает адрес:"
    warn "одноразовый код показывается прямо на странице входа."
    warn "Это сделано намеренно для проверяющих. Для долгой работы"
    warn "поставьте обратный прокси с TLS и уберите блок подсказки."
}

# ------------------------------------------------------------ эксплуатация

cmd_status() {
    require_host
    say "Состояние стенда"
    remote "$(compose_cmd) ps"
    info ""
    local health
    health="$(remote "curl -fsS http://localhost:$APP_PORT/api/health" 2>/dev/null \
              || curl -fsS -m 10 "$PUBLIC_URL/api/health" 2>/dev/null \
              || echo 'не отвечает')"
    info "Здоровье: $health"
    info "Адрес: $PUBLIC_URL"
}

cmd_logs() {
    require_host
    remote "$(compose_cmd) logs --tail ${1:-80} app"
}

cmd_restart() {
    require_host
    say "Перезапуск"
    remote "$(compose_cmd) restart app"
    wait_for_health
}

cmd_reset() {
    require_host
    say "Возврат к исходному набору вердиктов"
    warn "Будут удалены вердикты, решения и черновики заявок."
    printf '  Продолжить? [y/N] '
    read -r answer
    [ "$answer" = "y" ] || [ "$answer" = "Y" ] || die "отменено"

    local compose
    compose="$(compose_cmd)"
    remote "$compose exec -T db psql -U concorde -c 'TRUNCATE verdict CASCADE;'"
    remote "$compose exec -T app python -m scripts.06_seed_db"
    info "исходный набор загружен"
}

cmd_down() {
    require_host
    say "Остановка стенда"
    remote "$(compose_cmd) down"
    info "контейнеры остановлены, данные сохранены"
    info "для полного удаления данных: $(compose_cmd) down -v"
}

cmd_url() {
    require_host
    printf '%s\n' "$PUBLIC_URL"
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
  url             напечатать адрес стенда

Переменные окружения:
  CONCORDE_HOST       user@host удалённой машины (обязательно)
  CONCORDE_SSH_PORT   порт SSH, по умолчанию 22
  CONCORDE_DIR        каталог на стенде, по умолчанию concorde
  CONCORDE_APP_PORT   порт приложения, по умолчанию 8080
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
    url)             cmd_url ;;
    -h|--help|help)  usage ;;
    *)               usage; die "неизвестная команда: $1" ;;
esac
