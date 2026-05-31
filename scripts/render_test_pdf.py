"""
Script de test para el layout — genera PDF con datos de muestra.
No llama a Claude ni a Whisper. Costo: $0.

Uso:
    cd /home/user/Familia-marino-saraniti
    python scripts/render_test_pdf.py

Salida: /tmp/libro_test.pdf
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.agents.editor_agent import BookManuscript
from pipeline.agents import layout_agent

CAPITULO_LARGO = """
Hay casas que uno recuerda por el olor. La de mi infancia olía a madera mojada y a tortas fritas los domingos, un olor que todavía hoy, si aparece en algún lado, me lleva de vuelta sin escalas al patio de tierra, a la sombra del tilo, a la voz de mi madre llamando desde adentro.

Nací en un pueblo del interior que ya no existe como pueblo, o que existe pero reducido a unas pocas casas y una iglesia que nadie restauró. Cuando era chico me parecía enorme. Después volví de grande y entendí que había sido siempre pequeño, que era yo el que había crecido y la memoria la que había estirado todo.

Mi padre llegó antes que nosotros, como llegaron tantos. Cruzó con una valija de cartón y la dirección de un pariente escrita en un papel doblado cuatro veces. No hablaba el idioma, no conocía a nadie, tenía veintidós años. Cuando me contaba eso yo no lo podía creer. Yo a los veintidós no sabía ni planchar una camisa.

Lo que más me costó entender de grande es que el coraje no se siente como coraje cuando lo estás viviendo. Mi padre no pensó que era valiente cuando subió al barco. Pensó que no tenía otra opción. Esa diferencia me tardó años en comprenderla.

Trabajé desde los catorce. No por necesidad, aunque también había algo de eso, sino porque en mi casa el trabajo era la forma de estar en el mundo. No se hablaba de sentimientos, se hablaba de lo que había que hacer. Esa fue nuestra lengua.

Me casé joven, como se hacía. Tuvimos hijos, compramos la casa, pusimos el negocio. Hubo años buenos y años malos, como en todas las familias, pero nunca nos faltó lo esencial. Eso no es poco. Eso, con el tiempo, aprendí que es casi todo.

Lo que más valoro ahora, mirando para atrás, son las cosas pequeñas. Las cenas largas. Las partidas de truco. El viaje que hicimos al sur cuando los chicos eran chicos y llovió todo el tiempo y igual fue perfecto. La vida se esconde en esos detalles, no en los grandes momentos que uno cree que va a recordar.

A mis nietos les digo siempre lo mismo: no se apuren. El tiempo tiene una manera de voltearse que uno no ve venir. Un día estás apurado por llegar a algún lado y al día siguiente estás sentado acá, mirando el jardín, y te preguntás adónde fue todo. La respuesta es: no fue a ningún lado. Está acá, en cada cosa que hicieron, en la gente que quisieron, en lo que dejaron.

—Uno no sabe lo que tiene hasta que tiene que contárselo a alguien, y entonces se da cuenta de que tuvo mucho.

Eso es lo que este libro me enseñó. Y se los dejo a ustedes para que no tengan que aprenderlo tan tarde.
"""

CAPITULO_MEDIO = """
Crecí entre dos idiomas y ninguno del todo mío. En casa se hablaba uno; afuera, otro. Durante años viví en esa frontera sin nombre, buscando palabras que cruzaran de un lado al otro sin perder nada en el camino.

Mi madre decía que ella pensaba en el idioma de su infancia y soñaba en el de acá. Yo era al revés: pensaba acá pero cuando me enojaba, cuando me emocionaba, cuando algo me sacudía de verdad, volvía al otro. El idioma de los bordes, el que aparece cuando ya no queda tiempo para elegir.

Estudié porque no había otra forma de salir del barrio. No porque me lo dijeran, sino porque lo veía. Veía quiénes se quedaban y quiénes se iban. Quería irme, no del barrio en sí, sino de la sensación de que el mundo tenía un techo y el techo era bajo.

El trabajo me enseñó cosas que la universidad no supo. La universidad me enseñó a pensar; el trabajo me enseñó que pensar sin hacer no alcanza. Tardé en juntar las dos cosas. Cuando lo logré, algo se acomodó.

Me costó ser madre. No el amor, que llegó inmediato y sin aviso, sino todo lo demás: la incertidumbre permanente, la sensación de que cualquier decisión podía tener consecuencias que no iba a ver hasta veinte años después. Nadie te enseña eso. Nadie puede.

Lo que descubrí es que los hijos no necesitan que uno sea perfecto. Necesitan que uno esté. Esa es la diferencia entre lo que creía que iba a ser y lo que fui: creía que iba a ser perfecta; fui presente.

—Hay una cosa que le diría a la persona que era a los treinta: confiar más. No en los demás, que eso ya lo hacía. En mí.
"""

CAPITULO_CORTO = """
Nací de tarde, en invierno, durante un corte de luz. Eso ya dice algo de cómo empecé.

Me crié en una casa donde había muchos libros y poca plata. Aprendí que esas dos cosas no se contradicen, que a veces una compensa a la otra, aunque a los trece años hubiera preferido las zapatillas nuevas.

Fui el del medio, lo que significa que aprendí a ocupar el espacio sin pedirlo y a desaparecer cuando era necesario. Esas habilidades resultaron útiles más allá de la familia.

Mi carrera fue una sucesión de errores que, mirados en conjunto, parecen una dirección. En el momento no lo veía así. En el momento cada tropiezo era definitivo. Después entendí que la única forma de encontrar el camino es caminando, y que perderse es parte de eso.

Lo que más me cambió fue ser padre. No de la manera que uno espera, con revelaciones y momentos cinematográficos. Me cambió en lo cotidiano, en la manera de mirar las cosas pequeñas, en la paciencia que creía no tener y que apareció cuando fue necesaria.

Si tuviera que resumir lo que aprendí en todo este tiempo, diría esto: el afecto es la única cosa que crece cuando se comparte. Todo lo demás se divide.

—Eso es lo que quiero que quede de mí cuando ya no esté: que fui alguien que quiso bien.
"""

integrantes = [
    {"nombre": "Elena Rodríguez", "fecha_nac": "1945-03-12", "fecha_fallec": "", "rol": "abuela", "vive": True},
    {"nombre": "Carlos Rodríguez", "fecha_nac": "1972-07-08", "fecha_fallec": "", "rol": "hijo", "vive": True},
    {"nombre": "María Rodríguez", "fecha_nac": "1975-11-22", "fecha_fallec": "", "rol": "hija", "vive": True},
]

relaciones = [
    {"persona_a": "Elena Rodríguez", "relacion": "madre", "persona_b": "Carlos Rodríguez"},
    {"persona_a": "Elena Rodríguez", "relacion": "madre", "persona_b": "María Rodríguez"},
]

manuscript = BookManuscript(
    orden=["Elena Rodríguez", "Carlos Rodríguez", "María Rodríguez"],
    capitulos={
        "Elena Rodríguez": CAPITULO_LARGO,
        "Carlos Rodríguez": CAPITULO_MEDIO,
        "María Rodríguez": CAPITULO_CORTO,
    },
    prologo=(
        "Hay libros que se escriben para contar algo nuevo y otros que se escriben para que algo no se pierda. "
        "Este pertenece al segundo grupo.\n\n"
        "Las páginas que siguen nacieron de conversaciones. De tardes con un grabador encendido sobre la mesa, "
        "de mates que se enfriaban mientras alguien intentaba acordarse del nombre de una calle, del año de un "
        "casamiento, del color exacto de una cocina que ya no existe.\n\n"
        "Recordar es un trabajo extraño. Uno cree que va a buscar un dato y se encuentra con una escena entera. "
        "Las memorias familiares no se ordenan por fechas. Se ordenan por afectos, por casas, por sobremesas.\n\n"
        "Lo que hay en estas páginas es, sobre todo, voz. Voz hablada antes que escrita. He intentado conservar "
        "el modo en que cada quien dice lo suyo: las pausas, los rodeos, las risas que aparecen cuando se recuerda "
        "algo que en su momento fue grave y que el tiempo volvió cómico."
    ),
    epilogo=(
        "Cuando terminé de escuchar todas estas historias, me quedé un rato en silencio. No porque no supiera "
        "qué decir, sino porque lo que había escuchado pedía un momento de quietud antes de ser convertido en palabras.\n\n"
        "Una familia no es solo las personas que la componen. Es también el modo en que se miran, el idioma "
        "privado que desarrollan con el tiempo, los chistes que nadie de afuera entiende, los silencios que "
        "son más elocuentes que los discursos.\n\n"
        "Este libro no termina acá. Termina cuando el último de sus lectores lo cierre y piense en alguien "
        "que quiere, y decida contarle algo que todavía no le contó."
    ),
    transiciones={
        "Elena Rodríguez→Carlos Rodríguez": (
            "La historia de Elena es también, en cierto modo, el origen de la de Carlos. "
            "Crecemos sobre lo que otros construyeron, aunque no siempre lo sepamos."
        ),
        "Carlos Rodríguez→María Rodríguez": (
            "Entre hermanos hay una historia paralela que los padres solo ven en parte. "
            "Lo que sigue es esa otra historia."
        ),
    },
)

personas_meta = [
    {"nombre": "Elena Rodríguez", "fecha_nac": "1945-03-12", "rol": "abuela", "vive": True, "fecha_fallec": ""},
    {"nombre": "Carlos Rodríguez", "fecha_nac": "1972-07-08", "rol": "hijo", "vive": True, "fecha_fallec": ""},
    {"nombre": "María Rodríguez", "fecha_nac": "1975-11-22", "rol": "hija", "vive": True, "fecha_fallec": ""},
]

output = "/tmp/libro_test.pdf"
print("Generando PDF de prueba...")
path = layout_agent.run(
    manuscript=manuscript,
    personas_meta=personas_meta,
    nombre_familia="Familia Rodríguez · Test",
    output_path=output,
    todos_integrantes=integrantes,
    relaciones=relaciones,
)
print(f"PDF generado: {path}")
