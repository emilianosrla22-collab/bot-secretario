from flask import Flask, request
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import anthropic
import os
import json
from datetime import datetime, timedelta
import threading
import time

app = Flask(__name__)

# Configuración
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP_NUMBER = os.environ.get('TWILIO_WHATSAPP_NUMBER', 'whatsapp:+14155238886')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')
MI_NUMERO = os.environ.get('MI_NUMERO')  # ej: whatsapp:+5491112345678

# Cliente Anthropic
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Memoria de conversación y recordatorios (en memoria, simple)
conversaciones = {}  # número -> lista de mensajes
recordatorios = []   # lista de {datetime, mensaje, numero}

SYSTEM_PROMPT = """Sos un asistente personal por WhatsApp. Tu nombre es "Asistente".
Ayudás con:
- Recordatorios: cuando el usuario dice "recordame que el [fecha/hora] tengo [evento]", guardás el recordatorio y confirmás
- Tareas y notas: cuando pide que anotes algo, confirmás
- Preguntas generales: respondés con inteligencia

Para recordatorios, cuando detectes uno respondé SIEMPRE en este formato JSON al final de tu respuesta:
RECORDATORIO:{"fecha": "YYYY-MM-DD HH:MM", "mensaje": "descripción del evento"}

Hoy es: """ + datetime.now().strftime("%d/%m/%Y %H:%M") + """
Respondé siempre en español, de forma concisa y amigable."""


def enviar_whatsapp(numero, mensaje):
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    client.messages.create(
        body=mensaje,
        from_=TWILIO_WHATSAPP_NUMBER,
        to=numero
    )


def procesar_recordatorio(texto, numero):
    """Extrae y guarda recordatorio si Claude lo detectó"""
    if 'RECORDATORIO:' in texto:
        try:
            partes = texto.split('RECORDATORIO:')
            json_str = partes[1].strip().split('\n')[0]
            data = json.loads(json_str)
            fecha = datetime.strptime(data['fecha'], '%Y-%m-%d %H:%M')
            recordatorios.append({
                'datetime': fecha,
                'mensaje': data['mensaje'],
                'numero': numero
            })
            # Devolver texto limpio sin el JSON
            return partes[0].strip()
        except:
            return texto.replace('RECORDATORIO:', '').strip()
    return texto


def verificar_recordatorios():
    """Corre en background y manda recordatorios cuando llega la hora"""
    while True:
        ahora = datetime.now()
        for rec in recordatorios[:]:
            if ahora >= rec['datetime']:
                try:
                    enviar_whatsapp(
                        rec['numero'],
                        f"🔔 *Recordatorio:* {rec['mensaje']}"
                    )
                    recordatorios.remove(rec)
                except Exception as e:
                    print(f"Error enviando recordatorio: {e}")
        time.sleep(30)  # Verificar cada 30 segundos


@app.route('/webhook', methods=['POST'])
def webhook():
    numero = request.form.get('From')
    mensaje = request.form.get('Body', '').strip()

    if not mensaje:
        return str(MessagingResponse())

    # Historial de conversación
    if numero not in conversaciones:
        conversaciones[numero] = []

    conversaciones[numero].append({
        "role": "user",
        "content": mensaje
    })

    # Mantener solo los últimos 10 mensajes
    if len(conversaciones[numero]) > 10:
        conversaciones[numero] = conversaciones[numero][-10:]

    # Llamar a Claude
    try:
        respuesta = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            messages=conversaciones[numero]
        )
        texto = respuesta.content[0].text

        # Procesar recordatorio si lo hay
        texto_limpio = procesar_recordatorio(texto, numero)

        # Guardar respuesta en historial
        conversaciones[numero].append({
            "role": "assistant",
            "content": texto_limpio
        })

        # Responder por WhatsApp
        resp = MessagingResponse()
        resp.message(texto_limpio)
        return str(resp)

    except Exception as e:
        print(f"Error: {e}")
        resp = MessagingResponse()
        resp.message("Hubo un error, intentá de nuevo.")
        return str(resp)


@app.route('/', methods=['GET'])
def index():
    return "Bot activo ✅"


if __name__ == '__main__':
    # Iniciar thread de recordatorios
    thread = threading.Thread(target=verificar_recordatorios, daemon=True)
    thread.start()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
