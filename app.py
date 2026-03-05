from flask import Flask, request
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from groq import Groq
import os
import json
from datetime import datetime
import threading
import time

app = Flask(__name__)

TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP_NUMBER = os.environ.get('TWILIO_WHATSAPP_NUMBER', 'whatsapp:+14155238886')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')

cliente = Groq(api_key=GROQ_API_KEY)

conversaciones = {}
recordatorios = []

SYSTEM_PROMPT = """Sos un asistente personal por WhatsApp. Tu nombre es "Asistente".
Ayudás con:
- Recordatorios: cuando el usuario dice "recordame que el [fecha/hora] tengo [evento]", guardás el recordatorio y confirmás
- Tareas y notas
- Preguntas generales

Para recordatorios, al final de tu respuesta incluí SIEMPRE:
RECORDATORIO:{"fecha": "YYYY-MM-DD HH:MM", "mensaje": "descripción"}

Hoy es: """ + datetime.now().strftime("%d/%m/%Y %H:%M") + """
Respondé siempre en español, de forma concisa y amigable."""


def enviar_whatsapp(numero, mensaje):
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    client.messages.create(body=mensaje, from_=TWILIO_WHATSAPP_NUMBER, to=numero)


def procesar_recordatorio(texto, numero):
    if 'RECORDATORIO:' in texto:
        try:
            partes = texto.split('RECORDATORIO:')
            json_str = partes[1].strip().split('\n')[0]
            data = json.loads(json_str)
            fecha = datetime.strptime(data['fecha'], '%Y-%m-%d %H:%M')
            recordatorios.append({'datetime': fecha, 'mensaje': data['mensaje'], 'numero': numero})
            return partes[0].strip()
        except:
            return texto.replace('RECORDATORIO:', '').strip()
    return texto


def verificar_recordatorios():
    while True:
        ahora = datetime.now()
        for rec in recordatorios[:]:
            if ahora >= rec['datetime']:
                try:
                    enviar_whatsapp(rec['numero'], f"🔔 *Recordatorio:* {rec['mensaje']}")
                    recordatorios.remove(rec)
                except Exception as e:
                    print(f"Error: {e}")
        time.sleep(30)


@app.route('/webhook', methods=['POST'])
def webhook():
    numero = request.form.get('From')
    mensaje = request.form.get('Body', '').strip()

    if not mensaje:
        return str(MessagingResponse())

    if numero not in conversaciones:
        conversaciones[numero] = []

    conversaciones[numero].append({"role": "user", "content": mensaje})

    if len(conversaciones[numero]) > 10:
        conversaciones[numero] = conversaciones[numero][-10:]

    try:
        mensajes = [{"role": "system", "content": SYSTEM_PROMPT}] + conversaciones[numero]
        respuesta = cliente.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=mensajes,
            max_tokens=1000,
        )
        texto = respuesta.choices[0].message.content
        texto_limpio = procesar_recordatorio(texto, numero)
        conversaciones[numero].append({"role": "assistant", "content": texto_limpio})

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
    thread = threading.Thread(target=verificar_recordatorios, daemon=True)
    thread.start()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
