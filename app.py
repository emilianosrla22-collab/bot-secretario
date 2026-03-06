from flask import Flask, request
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from groq import Groq
import gspread
from google.oauth2.service_account import Credentials
import os
import json
from datetime import datetime, timedelta
import threading
import time

app = Flask(__name__)

TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP_NUMBER = os.environ.get('TWILIO_WHATSAPP_NUMBER', 'whatsapp:+14155238886')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')
MI_NUMERO = os.environ.get('MI_NUMERO')
GOOGLE_CREDENTIALS = os.environ.get('GOOGLE_CREDENTIALS')
SPREADSHEET_ID = os.environ.get('SPREADSHEET_ID')

cliente = Groq(api_key=GROQ_API_KEY)
conversaciones = {}
recordatorios = []


def get_sheets_client():
    creds_dict = json.loads(GOOGLE_CREDENTIALS)
    scopes = ['https://www.googleapis.com/auth/spreadsheets.readonly']
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)


def get_vencimientos_proximos(dias=7):
    """Lee Lista de Pagos y devuelve items que vencen en los próximos X días"""
    try:
        gc = get_sheets_client()
        sh = gc.open_by_key(SPREADSHEET_ID)
        ws = sh.worksheet('Lista de Pagos')
        datos = ws.get_all_values()
        
        hoy = datetime.now().date()
        limite = hoy + timedelta(days=dias)
        vencimientos = []
        
        for i, fila in enumerate(datos[8:], start=9):  # desde fila 9
            try:
                descripcion = fila[2] if len(fila) > 2 else ''
                fecha_str = fila[3] if len(fila) > 3 else ''
                condicion = fila[6] if len(fila) > 6 else ''
                
                if not descripcion or not fecha_str or condicion == 'PAGADO':
                    continue
                
                # Intentar parsear fecha
                for fmt in ['%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y']:
                    try:
                        fecha = datetime.strptime(fecha_str, fmt).date()
                        break
                    except:
                        fecha = None
                
                if fecha and hoy <= fecha <= limite:
                    diff = (fecha - hoy).days
                    total = fila[10] if len(fila) > 10 else ''
                    vencimientos.append({
                        'descripcion': descripcion,
                        'fecha': fecha.strftime('%d/%m/%Y'),
                        'dias': diff,
                        'total': total,
                        'condicion': condicion
                    })
            except:
                continue
        
        return vencimientos
    except Exception as e:
        print(f"Error leyendo Sheets: {e}")
        return []


def formatear_alerta(vencimientos):
    if not vencimientos:
        return "✅ No hay vencimientos en los próximos 7 días."
    
    msg = "🚨 *ALERTAS DE VENCIMIENTO*\n\n"
    for v in vencimientos:
        if v['dias'] == 0:
            dias_txt = "⚠️ *HOY*"
        elif v['dias'] == 1:
            dias_txt = "⚠️ *MAÑANA*"
        else:
            dias_txt = f"📅 en {v['dias']} días ({v['fecha']})"
        
        msg += f"{dias_txt}\n"
        msg += f"📌 {v['descripcion']}\n"
        if v['total']:
            msg += f"💰 {v['total']}\n"
        msg += "\n"
    
    return msg.strip()


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
    ultimo_aviso = None
    while True:
        ahora = datetime.now()
        
        # Mandar recordatorios del usuario
        for rec in recordatorios[:]:
            if ahora >= rec['datetime']:
                try:
                    enviar_whatsapp(rec['numero'], f"🔔 *Recordatorio:* {rec['mensaje']}")
                    recordatorios.remove(rec)
                except Exception as e:
                    print(f"Error: {e}")
        
        # Alerta diaria de vencimientos a las 10am
        if MI_NUMERO and SPREADSHEET_ID:
            hoy_str = ahora.strftime('%Y-%m-%d')
            if ahora.hour == 10 and ahora.minute < 1 and ultimo_aviso != hoy_str:
                try:
                    vencimientos = get_vencimientos_proximos(7)
                    mensaje = formatear_alerta(vencimientos)
                    enviar_whatsapp(MI_NUMERO, mensaje)
                    ultimo_aviso = hoy_str
                    print(f"Alerta diaria enviada: {hoy_str}")
                except Exception as e:
                    print(f"Error alerta diaria: {e}")
        
        time.sleep(30)


SYSTEM_PROMPT = """Sos un asistente personal por WhatsApp. Tu nombre es "Asistente".
Ayudás con:
- Recordatorios: cuando el usuario dice "recordame que el [fecha/hora] tengo [evento]"
- Consultas sobre vencimientos de pagos
- Tareas y notas
- Preguntas generales

Para recordatorios incluí SIEMPRE al final:
RECORDATORIO:{"fecha": "YYYY-MM-DD HH:MM", "mensaje": "descripción"}

Hoy es: """ + datetime.now().strftime("%d/%m/%Y %H:%M") + """
Respondé siempre en español, de forma concisa y amigable."""


@app.route('/webhook', methods=['POST'])
def webhook():
    numero = request.form.get('From')
    mensaje = request.form.get('Body', '').strip()

    if not mensaje:
        return str(MessagingResponse())

    # Comando especial para ver vencimientos
    if any(x in mensaje.lower() for x in ['vencimientos', 'que vence', 'qué vence', 'pagos pendientes', 'alertas']):
        vencimientos = get_vencimientos_proximos(7)
        texto = formatear_alerta(vencimientos)
        resp = MessagingResponse()
        resp.message(texto)
        return str(resp)

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
