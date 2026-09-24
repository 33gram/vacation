// Тонкая развязка между API-клиентом (middleware на 401, `api/client.ts`)
// и роутером (`app/router.tsx`): клиент не должен импортировать сам объект
// роутера напрямую — оба модуля тогда были бы взаимно зависимы (роутер
// подключает страницы, страницы вызывают клиент, клиент звал бы роутер).
// `router.tsx` регистрирует свою функцию навигации один раз при создании
// роутера; до регистрации (в основном — только в момент самого модульного
// импорта) переход просто не срабатывает.
type Navigate = (path: string) => void;

let navigate: Navigate | null = null;

export function registerNavigate(fn: Navigate): void {
  navigate = fn;
}

export function goToLogin(): void {
  navigate?.("/login");
}
