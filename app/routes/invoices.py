from datetime import date
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.database import get_db

router = APIRouter(prefix="/invoices", tags=["invoices"])

class InvoiceItem(BaseModel):
    product_id: int
    quantity: int = Field(gt=0, description="Quantity must be greater than 0")


class InvoiceCreate(BaseModel):
    invoice_no: str
    issue_date: date
    due_date: date
    client_id: int
    items: List[InvoiceItem]
    tax: float = Field(ge=0, description="Tax percentage (e.g., 10 for 10%)")


class InvoiceItemResponse(BaseModel):
    id: int
    product_id: int
    product_name: str
    quantity: int
    unit_price: float
    subtotal: float


class ClientResponse(BaseModel):
    id: int
    name: str
    address: str
    company_registration_no: str


class InvoiceResponse(BaseModel):
    id: int
    invoice_no: str
    issue_date: str
    due_date: str
    client: ClientResponse
    items: List[InvoiceItemResponse]
    tax: float
    subtotal: float
    tax_amount: float
    total: float


class InvoiceListItem(BaseModel):
    id: int
    invoice_no: str
    issue_date: str
    due_date: str
    client_name: str
    total: float


@router.post("", status_code=201, response_model=InvoiceResponse)
def create_invoice(invoice: InvoiceCreate):
    """
    Create a new invoice with items.
    """
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Validate client exists
            cursor.execute("SELECT id FROM clients WHERE id = ?", (invoice.client_id,))
            if cursor.fetchone() is None:
                raise HTTPException(status_code=404, detail=f"Client with id {invoice.client_id} not found")
            
            # Validate invoice_no is unique
            cursor.execute("SELECT id FROM invoices WHERE invoice_no = ?", (invoice.invoice_no,))
            if cursor.fetchone() is not None:
                raise HTTPException(status_code=400, detail=f"Invoice number {invoice.invoice_no} already exists")
            
            # Validate all products exist and calculate totals
            subtotal = 0
            validated_items = []
            
            for item in invoice.items:
                cursor.execute("SELECT id, name, price FROM products WHERE id = ?", (item.product_id,))
                product = cursor.fetchone()
                if product is None:
                    raise HTTPException(status_code=404, detail=f"Product with id {item.product_id} not found")
                
                item_subtotal = product["price"] * item.quantity
                subtotal += item_subtotal
                validated_items.append({
                    "product_id": item.product_id,
                    "product_name": product["name"],
                    "quantity": item.quantity,
                    "unit_price": product["price"],
                    "subtotal": item_subtotal
                })
            
            # Calculate tax and total
            tax_amount = subtotal * (invoice.tax / 100)
            total = subtotal + tax_amount
            
            # Insert invoice
            cursor.execute("""
                INSERT INTO invoices (invoice_no, issue_date, due_date, client_id, tax, total)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (invoice.invoice_no, invoice.issue_date, invoice.due_date, invoice.client_id, invoice.tax, total))
            
            invoice_id = cursor.lastrowid
            
            # Insert invoice items
            for item in invoice.items:
                cursor.execute("SELECT price FROM products WHERE id = ?", (item.product_id,))
                unit_price = cursor.fetchone()["price"]
                
                cursor.execute("""
                    INSERT INTO invoice_items (invoice_id, product_id, quantity, unit_price)
                    VALUES (?, ?, ?, ?)
                """, (invoice_id, item.product_id, item.quantity, unit_price))
            
            # Fetch the complete invoice for response
            return get_invoice(invoice_id)
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.get("", response_model=List[InvoiceListItem])
def list_invoices():
    """
    List all invoices with basic information.
    """
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    i.id,
                    i.invoice_no,
                    i.issue_date,
                    i.due_date,
                    i.total,
                    c.name as client_name
                FROM invoices i
                JOIN clients c ON i.client_id = c.id
                ORDER BY i.created_at DESC
            """)
            
            rows = cursor.fetchall()
            invoices = [
                {
                    "id": row["id"],
                    "invoice_no": row["invoice_no"],
                    "issue_date": row["issue_date"],
                    "due_date": row["due_date"],
                    "client_name": row["client_name"],
                    "total": row["total"]
                }
                for row in rows
            ]
            return invoices
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.get("/{invoice_id}", response_model=InvoiceResponse)
def get_invoice(invoice_id: int):
    """
    Get a single invoice by ID with full details.
    """
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Get invoice with client details
            cursor.execute("""
                SELECT 
                    i.id,
                    i.invoice_no,
                    i.issue_date,
                    i.due_date,
                    i.tax,
                    i.total,
                    c.id as client_id,
                    c.name as client_name,
                    c.address as client_address,
                    c.company_registration_no as client_registration_no
                FROM invoices i
                JOIN clients c ON i.client_id = c.id
                WHERE i.id = ?
            """, (invoice_id,))
            
            invoice_row = cursor.fetchone()
            if invoice_row is None:
                raise HTTPException(status_code=404, detail="Invoice not found")
            
            # Get invoice items
            cursor.execute("""
                SELECT 
                    ii.id,
                    ii.product_id,
                    ii.quantity,
                    ii.unit_price,
                    p.name as product_name
                FROM invoice_items ii
                JOIN products p ON ii.product_id = p.id
                WHERE ii.invoice_id = ?
            """, (invoice_id,))
            
            items_rows = cursor.fetchall()
            
            # Build response
            items = []
            subtotal = 0
            
            for item_row in items_rows:
                item_subtotal = item_row["unit_price"] * item_row["quantity"]
                subtotal += item_subtotal
                
                items.append({
                    "id": item_row["id"],
                    "product_id": item_row["product_id"],
                    "product_name": item_row["product_name"],
                    "quantity": item_row["quantity"],
                    "unit_price": item_row["unit_price"],
                    "subtotal": item_subtotal
                })
            
            tax_amount = subtotal * (invoice_row["tax"] / 100)
            
            return {
                "id": invoice_row["id"],
                "invoice_no": invoice_row["invoice_no"],
                "issue_date": invoice_row["issue_date"],
                "due_date": invoice_row["due_date"],
                "client": {
                    "id": invoice_row["client_id"],
                    "name": invoice_row["client_name"],
                    "address": invoice_row["client_address"],
                    "company_registration_no": invoice_row["client_registration_no"]
                },
                "items": items,
                "tax": invoice_row["tax"],
                "subtotal": subtotal,
                "tax_amount": tax_amount,
                "total": invoice_row["total"]
            }
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.delete("/{invoice_id}", status_code=204)
def delete_invoice(invoice_id: int):
    """
    Delete an invoice and its items.
    """
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Check if invoice exists
            cursor.execute("SELECT id FROM invoices WHERE id = ?", (invoice_id,))
            if cursor.fetchone() is None:
                raise HTTPException(status_code=404, detail="Invoice not found")
            
            # Delete invoice items (will cascade if foreign key is set up properly)
            cursor.execute("DELETE FROM invoice_items WHERE invoice_id = ?", (invoice_id,))
            
            # Delete invoice
            cursor.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
            
            return None
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
