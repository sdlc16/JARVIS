"""JARVIS -- puente nativo de Windows (proceso Windows, fuera de WSL2).

Servidor HTTP local autenticado que ejecuta acciones de escritorio reales
(aplicaciones, ventanas, teclado/raton, archivos, sistema) para el nucleo de
OpenJarvis que corre en WSL2. Ver CLAUDE.md "Arquitectura de dos procesos".

Principios de seguridad (minimo privilegio):
  * solo escucha en la interfaz virtual de WSL (o loopback), nunca 0.0.0.0;
  * token Bearer aleatorio guardado cifrado con DPAPI (no en texto plano);
  * origen validado contra la subred de WSL;
  * cada capacidad se puede desactivar en ``bridge_config.json``;
  * las acciones sensibles exigen ``confirmed: true`` (lo pone el nucleo solo
    tras la aprobacion del usuario);
  * archivos: solo raices permitidas, sin escapar por enlaces, y "borrar" =
    papelera de reciclaje (nunca borrado definitivo);
  * cada peticion queda en ``bridge.jsonl`` sin token ni texto tecleado.
"""

__version__ = "1.0.0"
